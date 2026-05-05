from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from services.body_service.backend import MockBodyBackend, VTSBodyBackend
from services.body_service.config import BodyServiceConfig, load_body_profile
from services.body_service.debug_view import DebugRecorder, panel_backend, panel_command, panel_error, panel_info, panel_speech
from services.body_service.idle_engine import IdleEngine
from services.body_service.intent_fuser import IntentFuser
from services.body_service.models import BodyCommand, DispatchPlan, SpeechEvent
from services.body_service.state_store import BodyStateStore


logger = logging.getLogger("services.body_service")


class BodyService:
    def __init__(self, config: BodyServiceConfig) -> None:
        self.config = config
        self.profile = load_body_profile(config.profile_path())
        self.state_store = BodyStateStore(
            body_id=config.body.body_id,
            history_limit=config.body.event_history_limit,
            profile_name=self.profile.name,
            idle_enabled=config.body.idle_enabled,
        )
        self.fuser = IntentFuser(self.profile)
        self.debug = DebugRecorder(
            enabled=config.debug.enabled,
            root_dir=config.paths.debug_dir,
            write_latest_state=config.debug.write_latest_state,
        )
        self._lock = threading.RLock()
        self._last_motion_at_ms: dict[str, float] = {}
        self._tracking_current: dict[str, float] = {}
        self._idle_engine = IdleEngine(tick_hz=self.profile.idle.tick_hz, on_tick=self._on_idle_tick)
        self.backend = self._build_backend()

    def start(self) -> None:
        if self.config.backend.connect_on_startup:
            try:
                self.backend.connect()
            except Exception as exc:
                if self.config.debug.print_panels:
                    panel_error("Body Startup", [f"backend connect failed: {exc}"])
        self._refresh_backend_health()
        self._idle_engine.start()
        if self.config.debug.print_panels:
            panel_info(
                "body_service",
                [
                    f"listen: {self.config.service.listen_host}:{self.config.service.listen_port}",
                    f"backend: {self.config.backend.type}",
                    f"profile: {self.profile.name}",
                    f"body_id: {self.profile.body_id}",
                ],
            )

    def close(self) -> None:
        self._idle_engine.stop()
        self.backend.close()

    def health(self) -> dict[str, Any]:
        backend = self._refresh_backend_health()
        state = self.state_store.snapshot()
        return {
            "status": "ok",
            "service": self.config.service.service_name,
            "body_id": self.profile.body_id,
            "active_trace_id": state.get("active_trace_id", ""),
            "active_generation_id": state.get("active_generation_id", 1),
            "current_expression": state.get("current_expression", "idle"),
            "current_motion": state.get("current_motion", "idle"),
            "idle_enabled": state.get("idle_enabled", True),
            "speaking": state.get("speaking", False),
            "speech_phase": state.get("speech_phase", "idle"),
            "backend": backend,
        }

    def state(self) -> dict[str, Any]:
        backend = self._refresh_backend_health()
        self.state_store.update_backend(backend)
        state = self.state_store.snapshot()
        self.debug.write_state(state)
        return state

    def handle_command(self, body: dict[str, Any]) -> dict[str, Any]:
        command = BodyCommand.from_dict(body)
        if not command.command_id:
            command.command_id = f"body_cmd_{int(time.time() * 1000)}"
        if not command.body_id:
            command.body_id = self.profile.body_id
        if self.state_store.is_stale(trace_id=command.trace_id, generation_id=command.generation_id):
            return {"status": "ignored", "reason": "stale_generation", "command_id": command.command_id}
        self.state_store.activate(trace_id=command.trace_id, generation_id=command.generation_id, segment_index=command.segment_index)
        self.state_store.record_command(command)
        if self.config.debug.print_panels:
            panel_command(
                "Body Command",
                [
                    f"type={command.command_type} trace={command.trace_id} gen={command.generation_id} seg={command.segment_index}",
                    f"expression={command.expression or '-'} motion={command.motion or '-'} sync_to_audio={command.sync_to_audio}",
                    f"commit_mode={command.commit_mode} intensity={command.intensity:.2f}",
                ],
            )
        if command.command_type == "speech_plan" and command.sync_to_audio:
            self.state_store.set_pending_speech_plan(command)
            self.state_store.update_presence(
                expression=command.expression or self.state_store.snapshot().get("current_expression", "idle"),
                motion=command.motion or self.state_store.snapshot().get("current_motion", "idle"),
            )
            state = self.state()
            self.debug.write_event(kind="command", payload=command.to_dict(), state=state)
            return {"status": "accepted", "command_id": command.command_id, "pending": True}
        if command.command_type == "set_baseline":
            self.state_store.update_baseline(expression=command.expression or "idle", motion=command.motion or "idle")
        plan = self.fuser.plan_for_command(command, self.state_store.snapshot())
        if plan is not None:
            self._apply_plan(command.trace_id, command.generation_id, plan)
        state = self.state()
        self.debug.write_event(kind="command", payload=command.to_dict(), state=state)
        return {"status": "accepted", "command_id": command.command_id, "pending": False}

    def handle_speech_event(self, body: dict[str, Any]) -> dict[str, Any]:
        event = SpeechEvent.from_dict(body)
        if self.state_store.is_stale(trace_id=event.trace_id, generation_id=event.generation_id):
            return {"status": "ignored", "reason": "stale_generation", "task_id": event.task_id}
        self.state_store.activate(trace_id=event.trace_id, generation_id=event.generation_id, segment_index=event.segment_index)
        self.state_store.record_speech_event(event)
        if self.config.debug.print_panels:
            panel_speech(
                "Speech Event",
                [
                    f"action={event.action} trace={event.trace_id} gen={event.generation_id} seg={event.segment_index}",
                    f"text={_truncate(event.text, 48)}",
                    f"revealed={int((event.metadata or {}).get('revealed_count', 0) or 0)} speaking={self.state_store.snapshot().get('speaking', False)}",
                ],
            )
        if event.action in {"clear", "turn_end"}:
            self.state_store.set_pending_speech_plan(None)
        plan = self.fuser.plan_for_speech_event(event, self.state_store.snapshot())
        self._apply_plan(event.trace_id, event.generation_id, plan)
        if event.action in {"segment_start", "segment_complete", "turn_end", "clear"}:
            clear_pending = event.action in {"segment_start", "clear", "turn_end"}
            if clear_pending:
                self.state_store.set_pending_speech_plan(None)
        state = self.state()
        self.debug.write_event(kind="speech_event", payload=event.to_dict(), state=state)
        return {"status": "accepted", "task_id": event.task_id}

    def cancel_trace(self, trace_id: str) -> dict[str, Any]:
        cancelled = self.state_store.clear_trace(trace_id)
        if cancelled:
            baseline_expression = str(self.state_store.snapshot().get("baseline_expression", "idle"))
            self._apply_plan(
                trace_id,
                int(self.state_store.snapshot().get("active_generation_id", 1) or 1),
                DispatchPlan(
                    operation="cancel_trace",
                    expression=baseline_expression,
                    motion="idle",
                    logical_tracking={"head_x": 0.0, "head_y": 0.0, "head_z": 0.0, "smile": 0.0},
                    speaking=False,
                    speech_phase="idle",
                    reactive_level=0.0,
                    metadata={"source": "body_service_cancel_trace"},
                ),
            )
        state = self.state()
        self.debug.write_event(kind="cancel_trace", payload={"trace_id": trace_id}, state=state)
        return {"status": "cancelled" if cancelled else "ignored", "trace_id": trace_id, "cancelled_task_count": 1 if cancelled else 0}

    def _apply_plan(self, trace_id: str, generation_id: int, plan: DispatchPlan) -> None:
        if plan.operation == "noop":
            return
        if plan.operation in {"apply_presence", "segment_complete", "turn_end", "clear", "cancel_trace"}:
            self.state_store.clear_idle_stage_runtime()
        speaking_state = plan.metadata.get("speaking_state") if isinstance(plan.metadata, dict) else None
        segment_accent_state = plan.metadata.get("segment_accent_state") if isinstance(plan.metadata, dict) else None
        if plan.metadata.get("clear_speaking_runtime"):
            self.state_store.clear_speaking_runtime()
        elif isinstance(speaking_state, dict):
            self.state_store.set_speaking_runtime(
                signature=speaking_state.get("signature") if isinstance(speaking_state.get("signature"), dict) else None,
                started_at_monotonic=_optional_float(speaking_state.get("started_at_monotonic")),
                completed_at_monotonic=_optional_float(speaking_state.get("completed_at_monotonic")),
                envelope_phase=str(speaking_state.get("envelope_phase", "")).strip() or None,
            )
            completed_at = _optional_float(speaking_state.get("completed_at_monotonic"))
            if completed_at is not None:
                self.state_store.mark_speaking_completed(completed_at_monotonic=completed_at)
        if plan.metadata.get("clear_segment_accent_runtime"):
            self.state_store.clear_segment_accent_runtime()
        elif isinstance(segment_accent_state, dict):
            self.state_store.set_segment_accent_runtime(
                signature=segment_accent_state.get("signature") if isinstance(segment_accent_state.get("signature"), dict) else None,
                started_at_monotonic=_optional_float(segment_accent_state.get("started_at_monotonic")),
                duration_seconds=_optional_float(segment_accent_state.get("duration_seconds")),
            )
        if plan.metadata.get("clear_segment_retarget_runtime"):
            self.state_store.clear_segment_retarget_runtime()
        elif isinstance(plan.metadata.get("segment_retarget_state"), dict):
            retarget_state = plan.metadata.get("segment_retarget_state")
            self.state_store.set_segment_retarget_runtime(
                signature=retarget_state.get("signature") if isinstance(retarget_state.get("signature"), dict) else None,
                started_at_monotonic=_optional_float(retarget_state.get("started_at_monotonic")),
                duration_seconds=_optional_float(retarget_state.get("duration_seconds")),
            )
        translated = self.fuser.translate_tracking(plan.logical_tracking)
        translated = self._interpolate_tracking(translated, mode="event", reset=plan.operation in {"clear", "turn_end", "cancel_trace"})
        defer_tracking_to_idle_tick = plan.operation in {"apply_presence", "segment_complete", "turn_end"}
        backend_tracking = {} if defer_tracking_to_idle_tick else translated
        hotkeys_triggered: list[str] = []
        if plan.motion:
            hotkey = self.profile.motions.get(plan.motion)
            if hotkey is not None and hotkey.hotkey and not self._motion_on_cooldown(plan.motion, hotkey.cooldown_ms):
                hotkeys_triggered.append(hotkey.hotkey)
        for hotkey in plan.hotkeys:
            if hotkey not in hotkeys_triggered:
                hotkeys_triggered.append(hotkey)
        for hotkey in hotkeys_triggered:
            try:
                self.backend.trigger_hotkey(hotkey)
                if self.config.debug.print_panels:
                    panel_backend("VTS Hotkey", [f"hotkey={hotkey}", f"trace={trace_id} gen={generation_id}", f"op={plan.operation}"])
            except Exception as exc:
                if self.config.debug.print_panels:
                    panel_error("VTS Hotkey", [f"hotkey={hotkey}", str(exc)])
        if backend_tracking:
            try:
                self.backend.apply_tracking(backend_tracking, source=plan.metadata.get("source", "body_service"))
            except Exception as exc:
                if self.config.debug.print_panels:
                    panel_error("VTS Tracking", [f"trace={trace_id} gen={generation_id}", str(exc)])
        self.state_store.update_presence(
            expression=plan.expression,
            motion=plan.motion,
            speaking=plan.speaking,
            speech_phase=plan.speech_phase,
            reactive_level=plan.reactive_level,
            clear_pending=plan.operation in {"apply_presence", "clear", "turn_end", "cancel_trace"},
        )
        summary = {
            "operation": plan.operation,
            "trace_id": trace_id,
            "generation_id": generation_id,
            "hotkeys": hotkeys_triggered,
            "parameters": translated,
            "backend_parameters": backend_tracking,
            "defer_tracking_to_idle_tick": defer_tracking_to_idle_tick,
            "speaking": self.state_store.snapshot().get("speaking", False),
            "speech_phase": self.state_store.snapshot().get("speech_phase", "idle"),
            "reactive_level": self.state_store.snapshot().get("reactive_level", 0.0),
            "speaking_signature": self.state_store.snapshot().get("speaking_signature"),
            "speaking_envelope_phase": self.state_store.snapshot().get("speaking_envelope_phase", "idle"),
            "backend_ready": bool(self._refresh_backend_health().get("backend_ready", False)),
            "metadata": plan.metadata,
        }
        self.state_store.set_dispatch_summary(summary)

    def _on_idle_tick(self) -> None:
        if not self.config.body.idle_enabled:
            return
        snapshot = self.state_store.snapshot()
        snapshot = self._ensure_idle_stage(snapshot)
        snapshot = self._ensure_idle_wander(snapshot)
        snapshot = self._ensure_idle_attention(snapshot)
        snapshot = self._ensure_idle_glance(snapshot)
        frame = self.fuser.build_idle_frame(snapshot)
        translated = self.fuser.translate_tracking(frame.logical_tracking)
        try:
            if translated:
                smoothed = self._interpolate_tracking(translated, mode="speaking" if frame.speech_phase == "speaking" else "idle")
                self.backend.apply_tracking(smoothed, source="body_service_idle")
        except Exception:
            return
        if frame.clear_speaking_signature:
            baseline_expression = str(snapshot.get("baseline_expression", "idle"))
            self.state_store.clear_speaking_runtime()
            self.state_store.clear_segment_accent_runtime()
            self.state_store.clear_segment_retarget_runtime()
            self.state_store.update_presence(
                expression=baseline_expression,
                motion="idle",
                speaking=False,
                speech_phase="idle",
                reactive_level=0.0,
            )
            self.state_store.clear_idle_stage_runtime()
        timestamp = _now_iso()
        self.state_store.update_idle(summary=frame.summary, at_iso=timestamp)
        state = self.state_store.snapshot()
        dispatch = dict(state.get("active_dispatch_summary") or {})
        dispatch["idle_refresh"] = {
            "operation": "apply_idle_state",
            "trace_id": state.get("active_trace_id", ""),
            "generation_id": state.get("active_generation_id", 1),
            "parameters": smoothed if translated else {},
            "idle_state": frame.summary,
            "reactive_level": frame.reactive_level,
            "speech_phase": frame.speech_phase,
            "speaking_signature": frame.speaking_signature,
            "envelope_summary": frame.envelope_summary,
            "stage_summary": frame.stage_summary,
            "backend_ready": bool(state.get("backend", {}).get("backend_ready", False)),
            "metadata": {"source": "body_service_idle"},
        }
        self.state_store.set_dispatch_summary(dispatch)
        self.debug.write_state(self.state_store.snapshot())

    def _build_backend(self):
        backend_type = self.config.backend.type
        if backend_type == "mock":
            return MockBodyBackend(body_id=self.profile.body_id)
        if backend_type == "vts":
            return VTSBodyBackend(
                ws_url=self.config.backend.vts.ws_url,
                plugin_name=self.config.backend.vts.plugin_name,
                plugin_developer=self.config.backend.vts.plugin_developer,
                auth_token_path=self.config.paths.auth_token_path,
                connect_timeout_ms=self.config.backend.vts.connect_timeout_ms,
                request_timeout_ms=self.config.backend.vts.request_timeout_ms,
                model_hint=self.profile.model_hint,
            )
        raise ValueError(f"unsupported backend type: {backend_type}")

    def _refresh_backend_health(self) -> dict[str, Any]:
        try:
            backend_state = self.backend.probe()
        except Exception as exc:
            backend_state = {
                "backend_ready": False,
                "backend_type": self.config.backend.type,
                "last_error": str(exc),
            }
        backend_state["current_profile"] = self.profile.name
        backend_state["profile_path"] = str(self.profile.path)
        backend_state["model_hint"] = self.profile.model_hint
        if self.config.backend.type == "vts":
            backend_state["vts_ws_url"] = self.config.backend.vts.ws_url
        self.state_store.update_backend(backend_state)
        return backend_state

    def _motion_on_cooldown(self, motion_name: str, cooldown_ms: int) -> bool:
        if cooldown_ms <= 0:
            return False
        now_ms = time.monotonic() * 1000
        last = self._last_motion_at_ms.get(motion_name, 0.0)
        if now_ms - last < cooldown_ms:
            return True
        self._last_motion_at_ms[motion_name] = now_ms
        return False

    def _ensure_idle_wander(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if str(snapshot.get("speech_phase", "idle")) in {"speaking", "bridged_pause", "cooldown"}:
            return snapshot
        now = time.monotonic()
        started_at = float(snapshot.get("idle_wander_started_at_monotonic") or 0.0)
        duration = float(snapshot.get("idle_wander_duration_seconds") or 0.0)
        if started_at > 0 and duration > 0 and (now - started_at) < duration:
            return snapshot
        counter = int(snapshot.get("idle_wander_counter", 0) or 0) + 1
        signature = self.fuser.build_idle_wander_signature(body_id=self.profile.body_id, counter=counter)
        self.state_store.set_idle_wander_runtime(
            counter=counter,
            signature=signature,
            started_at_monotonic=now,
            duration_seconds=float(signature.get("duration_seconds", self.profile.idle.wander_duration_min_s) or self.profile.idle.wander_duration_min_s),
        )
        return self.state_store.snapshot()

    def _ensure_idle_attention(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if str(snapshot.get("speech_phase", "idle")) != "idle":
            return snapshot
        if snapshot.get("idle_stage_signature"):
            return snapshot
        now = time.monotonic()
        signature = snapshot.get("idle_attention_signature")
        started_at = float(snapshot.get("idle_attention_started_at_monotonic") or 0.0)
        duration = float(snapshot.get("idle_attention_duration_seconds") or 0.0)
        if isinstance(signature, dict) and started_at > 0 and duration > 0 and (now - started_at) < duration:
            return snapshot
        counter = int(snapshot.get("idle_attention_counter", 0) or 0) + 1
        attention = self.fuser.build_idle_attention_signature(
            body_id=self.profile.body_id,
            counter=counter,
            last_target_head_x=float(snapshot.get("idle_attention_last_target_head_x", 0.0) or 0.0),
            last_target_head_y=float(snapshot.get("idle_attention_last_target_head_y", 0.0) or 0.0),
            last_target_head_z=float(snapshot.get("idle_attention_last_target_head_z", 0.0) or 0.0),
        )
        self.state_store.set_idle_attention_runtime(
            counter=counter,
            signature=attention,
            started_at_monotonic=now,
            duration_seconds=float(attention.get("duration_seconds", self.profile.idle.attention_duration_min_s) or self.profile.idle.attention_duration_min_s),
        )
        return self.state_store.snapshot()

    def _ensure_idle_glance(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        now = time.monotonic()
        if str(snapshot.get("speech_phase", "idle")) != "idle":
            return snapshot
        if snapshot.get("idle_stage_signature"):
            return snapshot
        signature = snapshot.get("idle_glance_signature")
        started_at = float(snapshot.get("idle_glance_started_at_monotonic") or 0.0)
        duration = float(snapshot.get("idle_glance_duration_seconds") or 0.0)
        if isinstance(signature, dict) and started_at > 0 and duration > 0 and (now - started_at) >= duration:
            self.state_store.clear_idle_glance_runtime()
            snapshot = self.state_store.snapshot()
        if snapshot.get("idle_glance_signature"):
            return snapshot
        next_check_at = float(snapshot.get("idle_glance_next_check_at_monotonic") or 0.0)
        if next_check_at > 0 and now < next_check_at:
            return snapshot
        counter = int(snapshot.get("idle_glance_counter", 0) or 0) + 1
        glance = self.fuser.build_idle_glance_signature(body_id=self.profile.body_id, counter=counter)
        self.state_store.set_idle_glance_runtime(
            counter=counter,
            probe_counter=counter,
            signature=glance,
            started_at_monotonic=now,
            duration_seconds=float(glance.get("duration_seconds", self.profile.idle.glance_duration_min_s) or self.profile.idle.glance_duration_min_s),
        )
        self.state_store.defer_idle_glance_probe(
            probe_counter=counter,
            next_check_at_monotonic=now + self.fuser.next_idle_glance_delay_seconds(counter=counter),
        )
        return self.state_store.snapshot()

    def _ensure_idle_stage(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        runtime = self.profile.idle_stage_runtime
        if not runtime.enabled or not self.profile.idle_stages:
            return snapshot
        if str(snapshot.get("speech_phase", "idle")) != "idle":
            if snapshot.get("idle_stage_signature"):
                self.state_store.clear_idle_stage_runtime()
                return self.state_store.snapshot()
            return snapshot
        if not bool(snapshot.get("backend", {}).get("backend_ready", False)):
            backend = self._refresh_backend_health()
            snapshot = self.state_store.snapshot()
            if not bool(backend.get("backend_ready", False)):
                return snapshot
        now = time.monotonic()
        speaking_end_at = float(snapshot.get("idle_stage_last_speaking_end_at_monotonic") or 0.0)
        if speaking_end_at > 0 and (now - speaking_end_at) < runtime.suppression_after_speaking_s:
            return snapshot
        stage_signature = snapshot.get("idle_stage_signature")
        started_at = float(snapshot.get("idle_stage_started_at_monotonic") or 0.0)
        duration = float(snapshot.get("idle_stage_duration_seconds") or 0.0)
        if isinstance(stage_signature, dict) and started_at > 0 and duration > 0:
            if (now - started_at) < duration:
                return snapshot
            self.state_store.complete_idle_stage_runtime(
                completed_at_monotonic=now,
                next_allowed_at_monotonic=now + self.fuser.next_idle_stage_delay_seconds(counter=int(snapshot.get("idle_stage_counter", 0) or 0) + 1),
            )
            snapshot = self.state_store.snapshot()
        next_allowed_at = float(snapshot.get("idle_stage_next_allowed_at_monotonic") or 0.0)
        if next_allowed_at > 0 and now < next_allowed_at:
            return snapshot

        counter = int(snapshot.get("idle_stage_counter", 0) or 0) + 1
        stage_name = self.fuser.choose_idle_stage_name(counter=counter)
        if not stage_name:
            self.state_store.set_idle_stage_next_allowed_at(
                next_allowed_at_monotonic=now + self.fuser.next_idle_stage_delay_seconds(counter=counter),
            )
            return self.state_store.snapshot()
        signature = self.fuser.build_idle_stage_signature(body_id=self.profile.body_id, counter=counter, stage_name=stage_name)
        if not signature:
            self.state_store.set_idle_stage_next_allowed_at(
                next_allowed_at_monotonic=now + self.fuser.next_idle_stage_delay_seconds(counter=counter),
            )
            return self.state_store.snapshot()
        motion_name = str(signature.get("motion", "")).strip()
        hotkey_name = ""
        if runtime.allow_hotkey and motion_name:
            hotkey_name = self._trigger_idle_stage_motion(motion_name, float(signature.get("motion_intensity", 0.55) or 0.55))
        self.state_store.set_idle_stage_runtime(
            counter=counter,
            stage_name=stage_name,
            signature=signature,
            started_at_monotonic=now,
            duration_seconds=float(signature.get("duration_seconds", 1.6) or 1.6),
            expression=str(signature.get("expression", "")).strip(),
            motion=motion_name,
            hotkey=hotkey_name,
        )
        return self.state_store.snapshot()

    def _trigger_idle_stage_motion(self, motion_name: str, intensity: float) -> str:
        motion = self.profile.motions.get(motion_name)
        if motion is None or not motion.hotkey:
            return ""
        if self._motion_on_cooldown(motion_name, motion.cooldown_ms):
            return ""
        try:
            self.backend.trigger_hotkey(motion.hotkey)
            if self.config.debug.print_panels:
                panel_backend(
                    "VTS Hotkey",
                    [
                        f"hotkey={motion.hotkey}",
                        "trace=idle-stage",
                        f"motion={motion_name} intensity={intensity:.2f}",
                    ],
                )
            return motion.hotkey
        except Exception as exc:
            if self.config.debug.print_panels:
                panel_error("VTS Hotkey", [f"hotkey={motion.hotkey}", str(exc)])
            return ""

    def _interpolate_tracking(self, target: dict[str, float], *, mode: str, reset: bool = False) -> dict[str, float]:
        if not target:
            if reset:
                self._tracking_current.clear()
            return {}
        if reset or not self._tracking_current:
            self._tracking_current = {key: float(value) for key, value in target.items()}
            return {key: round(value, 4) for key, value in self._tracking_current.items()}

        blended: dict[str, float] = {}
        keys = set(self._tracking_current) | set(target)
        for key in keys:
            current = float(self._tracking_current.get(key, target.get(key, 0.0)))
            goal = float(target.get(key, current))
            alpha = self._tracking_alpha_for_key(key=key, mode=mode)
            current += (goal - current) * alpha
            blended[key] = round(current, 4)
        self._tracking_current = blended
        return dict(blended)

    def _tracking_alpha_for_key(self, *, key: str, mode: str) -> float:
        idle = self.profile.idle
        group = "head"
        if "Eye" in key:
            group = "eye"
        elif "Smile" in key or "Mouth" in key:
            group = "smile"
        elif key.endswith("Z"):
            group = "body"
        if mode == "event":
            mapping = {
                "head": idle.tracking_alpha_head_event,
                "body": idle.tracking_alpha_body_event,
                "eye": idle.tracking_alpha_eye_event,
                "smile": idle.tracking_alpha_smile_event,
            }
        elif mode == "speaking":
            mapping = {
                "head": idle.tracking_alpha_head_speaking,
                "body": idle.tracking_alpha_body_speaking,
                "eye": idle.tracking_alpha_eye_speaking,
                "smile": idle.tracking_alpha_smile_speaking,
            }
        else:
            mapping = {
                "head": idle.tracking_alpha_head_idle,
                "body": idle.tracking_alpha_body_idle,
                "eye": idle.tracking_alpha_eye_idle,
                "smile": idle.tracking_alpha_smile_idle,
            }
        alpha = float(mapping[group])
        if mode == "speaking":
            state = self.state_store.snapshot()
            if isinstance(state.get("segment_retarget_signature"), dict):
                alpha *= 1.28 if group in {"head", "eye"} else 1.12
            elif isinstance(state.get("segment_accent_signature"), dict):
                alpha *= 1.14
        elif mode == "idle":
            state = self.state_store.snapshot()
            if isinstance(state.get("idle_glance_signature"), dict):
                alpha *= 1.4 if group in {"head", "eye"} else 1.1
            if isinstance(state.get("idle_stage_signature"), dict):
                alpha *= 1.24 if group in {"head", "eye"} else 1.08
        return max(0.0, min(1.0, alpha))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _truncate(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3] + "..."


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)
