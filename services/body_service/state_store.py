from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

from services.body_service.models import BodyCommand, SpeechEvent


class BodyStateStore:
    def __init__(self, *, body_id: str, history_limit: int, profile_name: str, idle_enabled: bool) -> None:
        self._lock = threading.RLock()
        self._history_limit = history_limit
        self._state: dict[str, Any] = {
            "body_id": body_id,
            "active_trace_id": "",
            "active_generation_id": 1,
            "active_segment_index": None,
            "baseline_profile": "neutral",
            "baseline_expression": "idle",
            "baseline_motion": "idle",
            "current_expression": "idle",
            "current_motion": "idle",
            "pending_speech_plan": None,
            "speaking": False,
            "speech_phase": "idle",
            "reactive_level": 0.0,
            "speaking_signature": None,
            "speaking_started_at_monotonic": None,
            "speaking_completed_at_monotonic": None,
            "speaking_envelope_phase": "idle",
            "idle_enabled": idle_enabled,
            "idle_summary": {},
            "last_idle_tick_at": "",
            "idle_wander_counter": 0,
            "idle_wander_signature": None,
            "idle_wander_started_at_monotonic": None,
            "idle_wander_duration_seconds": None,
            "idle_attention_counter": 0,
            "idle_attention_signature": None,
            "idle_attention_started_at_monotonic": None,
            "idle_attention_duration_seconds": None,
            "idle_attention_last_target_head_x": 0.0,
            "idle_attention_last_target_head_y": 0.0,
            "idle_attention_last_target_head_z": 0.0,
            "idle_glance_counter": 0,
            "idle_glance_probe_counter": 0,
            "idle_glance_signature": None,
            "idle_glance_started_at_monotonic": None,
            "idle_glance_duration_seconds": None,
            "idle_glance_next_check_at_monotonic": None,
            "idle_stage_name": "",
            "idle_stage_signature": None,
            "idle_stage_started_at_monotonic": None,
            "idle_stage_duration_seconds": None,
            "idle_stage_phase": "idle",
            "idle_stage_expression": "",
            "idle_stage_motion": "",
            "idle_stage_hotkey": "",
            "idle_stage_last_completed_at_monotonic": None,
            "idle_stage_next_allowed_at_monotonic": None,
            "idle_stage_last_speaking_end_at_monotonic": None,
            "idle_stage_counter": 0,
            "segment_accent_signature": None,
            "segment_accent_started_at_monotonic": None,
            "segment_accent_duration_seconds": None,
            "segment_retarget_signature": None,
            "segment_retarget_started_at_monotonic": None,
            "segment_retarget_duration_seconds": None,
            "last_command": None,
            "last_speech_event": None,
            "active_dispatch_summary": None,
            "recent_events": [],
            "backend": {
                "backend_type": "uninitialized",
                "backend_ready": False,
                "current_profile": profile_name,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def is_stale(self, *, trace_id: str, generation_id: int) -> bool:
        with self._lock:
            active_trace = str(self._state.get("active_trace_id", ""))
            active_generation = int(self._state.get("active_generation_id", 1) or 1)
            return bool(active_trace and trace_id == active_trace and generation_id < active_generation)

    def activate(self, *, trace_id: str, generation_id: int, segment_index: int | None) -> None:
        with self._lock:
            active_trace = str(self._state.get("active_trace_id", ""))
            active_generation = int(self._state.get("active_generation_id", 1) or 1)
            if trace_id != active_trace or generation_id > active_generation:
                self._reset_speaking_runtime_unlocked()
                self._state["pending_speech_plan"] = None
            self._state["active_trace_id"] = trace_id
            self._state["active_generation_id"] = generation_id
            self._state["active_segment_index"] = segment_index

    def record_command(self, command: BodyCommand) -> None:
        payload = command.to_dict()
        payload["kind"] = "command"
        with self._lock:
            self._state["last_command"] = payload
            self._append_event(payload)

    def record_speech_event(self, event: SpeechEvent) -> None:
        payload = event.to_dict()
        payload["kind"] = "speech_event"
        with self._lock:
            self._state["last_speech_event"] = payload
            self._append_event(payload)

    def set_pending_speech_plan(self, command: BodyCommand | None) -> None:
        with self._lock:
            self._state["pending_speech_plan"] = command.to_dict() if command is not None else None

    def update_presence(
        self,
        *,
        expression: str | None = None,
        motion: str | None = None,
        speaking: bool | None = None,
        speech_phase: str | None = None,
        reactive_level: float | None = None,
        clear_pending: bool = False,
    ) -> None:
        with self._lock:
            if expression is not None:
                self._state["current_expression"] = expression
            if motion is not None:
                self._state["current_motion"] = motion
            if speaking is not None:
                self._state["speaking"] = speaking
            if speech_phase is not None:
                self._state["speech_phase"] = speech_phase
            if reactive_level is not None:
                self._state["reactive_level"] = round(float(reactive_level), 4)
            if clear_pending:
                self._state["pending_speech_plan"] = None

    def update_baseline(self, *, profile_name: str | None = None, expression: str | None = None, motion: str | None = None) -> None:
        with self._lock:
            if profile_name is not None:
                self._state["baseline_profile"] = profile_name
            if expression is not None:
                self._state["baseline_expression"] = expression
            if motion is not None:
                self._state["baseline_motion"] = motion

    def update_idle(self, *, summary: dict[str, float], at_iso: str) -> None:
        with self._lock:
            self._state["idle_summary"] = dict(summary)
            self._state["last_idle_tick_at"] = at_iso

    def set_idle_stage_runtime(
        self,
        *,
        counter: int,
        stage_name: str,
        signature: dict[str, Any],
        started_at_monotonic: float,
        duration_seconds: float,
        expression: str,
        motion: str,
        hotkey: str,
    ) -> None:
        with self._lock:
            self._state["idle_stage_counter"] = int(counter)
            self._state["idle_stage_name"] = stage_name
            self._state["idle_stage_signature"] = deepcopy(signature)
            self._state["idle_stage_started_at_monotonic"] = float(started_at_monotonic)
            self._state["idle_stage_duration_seconds"] = float(duration_seconds)
            self._state["idle_stage_phase"] = "active"
            self._state["idle_stage_expression"] = expression
            self._state["idle_stage_motion"] = motion
            self._state["idle_stage_hotkey"] = hotkey

    def complete_idle_stage_runtime(self, *, completed_at_monotonic: float, next_allowed_at_monotonic: float) -> None:
        with self._lock:
            self._state["idle_stage_last_completed_at_monotonic"] = float(completed_at_monotonic)
            self._state["idle_stage_next_allowed_at_monotonic"] = float(next_allowed_at_monotonic)
            self._state["idle_stage_name"] = ""
            self._state["idle_stage_signature"] = None
            self._state["idle_stage_started_at_monotonic"] = None
            self._state["idle_stage_duration_seconds"] = None
            self._state["idle_stage_phase"] = "idle"
            self._state["idle_stage_expression"] = ""
            self._state["idle_stage_motion"] = ""
            self._state["idle_stage_hotkey"] = ""

    def clear_idle_stage_runtime(self) -> None:
        with self._lock:
            self._state["idle_stage_name"] = ""
            self._state["idle_stage_signature"] = None
            self._state["idle_stage_started_at_monotonic"] = None
            self._state["idle_stage_duration_seconds"] = None
            self._state["idle_stage_phase"] = "idle"
            self._state["idle_stage_expression"] = ""
            self._state["idle_stage_motion"] = ""
            self._state["idle_stage_hotkey"] = ""

    def set_idle_stage_next_allowed_at(self, *, next_allowed_at_monotonic: float) -> None:
        with self._lock:
            self._state["idle_stage_next_allowed_at_monotonic"] = float(next_allowed_at_monotonic)

    def mark_speaking_completed(self, *, completed_at_monotonic: float | None) -> None:
        with self._lock:
            self._state["idle_stage_last_speaking_end_at_monotonic"] = float(completed_at_monotonic) if completed_at_monotonic is not None else None

    def set_idle_wander_runtime(
        self,
        *,
        counter: int,
        signature: dict[str, Any],
        started_at_monotonic: float,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            self._state["idle_wander_counter"] = int(counter)
            self._state["idle_wander_signature"] = deepcopy(signature)
            self._state["idle_wander_started_at_monotonic"] = float(started_at_monotonic)
            self._state["idle_wander_duration_seconds"] = float(duration_seconds)

    def update_backend(self, backend_state: dict[str, Any]) -> None:
        with self._lock:
            current_profile = self._state.get("backend", {}).get("current_profile", "")
            merged = dict(self._state.get("backend", {}))
            merged.update(backend_state)
            if current_profile and "current_profile" not in backend_state:
                merged["current_profile"] = current_profile
            self._state["backend"] = merged

    def set_dispatch_summary(self, summary: dict[str, Any]) -> None:
        with self._lock:
            self._state["active_dispatch_summary"] = summary

    def set_idle_attention_runtime(
        self,
        *,
        counter: int,
        signature: dict[str, Any],
        started_at_monotonic: float,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            self._state["idle_attention_counter"] = int(counter)
            self._state["idle_attention_signature"] = deepcopy(signature)
            self._state["idle_attention_started_at_monotonic"] = float(started_at_monotonic)
            self._state["idle_attention_duration_seconds"] = float(duration_seconds)
            self._state["idle_attention_last_target_head_x"] = float(signature.get("head_x", 0.0) or 0.0)
            self._state["idle_attention_last_target_head_y"] = float(signature.get("head_y", 0.0) or 0.0)
            self._state["idle_attention_last_target_head_z"] = float(signature.get("head_z", 0.0) or 0.0)

    def clear_idle_attention_runtime(self) -> None:
        with self._lock:
            self._state["idle_attention_signature"] = None
            self._state["idle_attention_started_at_monotonic"] = None
            self._state["idle_attention_duration_seconds"] = None

    def set_idle_glance_runtime(
        self,
        *,
        counter: int,
        probe_counter: int,
        signature: dict[str, Any],
        started_at_monotonic: float,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            self._state["idle_glance_counter"] = int(counter)
            self._state["idle_glance_probe_counter"] = int(probe_counter)
            self._state["idle_glance_signature"] = deepcopy(signature)
            self._state["idle_glance_started_at_monotonic"] = float(started_at_monotonic)
            self._state["idle_glance_duration_seconds"] = float(duration_seconds)
            self._state["idle_glance_next_check_at_monotonic"] = None

    def defer_idle_glance_probe(self, *, probe_counter: int, next_check_at_monotonic: float) -> None:
        with self._lock:
            self._state["idle_glance_probe_counter"] = int(probe_counter)
            self._state["idle_glance_next_check_at_monotonic"] = float(next_check_at_monotonic)

    def clear_idle_glance_runtime(self) -> None:
        with self._lock:
            self._state["idle_glance_signature"] = None
            self._state["idle_glance_started_at_monotonic"] = None
            self._state["idle_glance_duration_seconds"] = None

    def set_segment_accent_runtime(
        self,
        *,
        signature: dict[str, Any] | None,
        started_at_monotonic: float | None,
        duration_seconds: float | None,
    ) -> None:
        with self._lock:
            self._state["segment_accent_signature"] = deepcopy(signature) if signature is not None else None
            self._state["segment_accent_started_at_monotonic"] = float(started_at_monotonic) if started_at_monotonic is not None else None
            self._state["segment_accent_duration_seconds"] = float(duration_seconds) if duration_seconds is not None else None

    def clear_segment_accent_runtime(self) -> None:
        with self._lock:
            self._state["segment_accent_signature"] = None
            self._state["segment_accent_started_at_monotonic"] = None
            self._state["segment_accent_duration_seconds"] = None

    def set_segment_retarget_runtime(
        self,
        *,
        signature: dict[str, Any] | None,
        started_at_monotonic: float | None,
        duration_seconds: float | None,
    ) -> None:
        with self._lock:
            self._state["segment_retarget_signature"] = deepcopy(signature) if signature is not None else None
            self._state["segment_retarget_started_at_monotonic"] = float(started_at_monotonic) if started_at_monotonic is not None else None
            self._state["segment_retarget_duration_seconds"] = float(duration_seconds) if duration_seconds is not None else None

    def clear_segment_retarget_runtime(self) -> None:
        with self._lock:
            self._state["segment_retarget_signature"] = None
            self._state["segment_retarget_started_at_monotonic"] = None
            self._state["segment_retarget_duration_seconds"] = None

    def set_speaking_runtime(
        self,
        *,
        signature: dict[str, Any] | None = None,
        started_at_monotonic: float | None = None,
        completed_at_monotonic: float | None = None,
        envelope_phase: str | None = None,
    ) -> None:
        with self._lock:
            if signature is not None:
                self._state["speaking_signature"] = deepcopy(signature)
            if started_at_monotonic is not None:
                self._state["speaking_started_at_monotonic"] = float(started_at_monotonic)
                self._state["speaking_completed_at_monotonic"] = None
            if completed_at_monotonic is not None:
                self._state["speaking_completed_at_monotonic"] = float(completed_at_monotonic)
            if envelope_phase is not None:
                self._state["speaking_envelope_phase"] = envelope_phase

    def clear_speaking_runtime(self) -> None:
        with self._lock:
            self._reset_speaking_runtime_unlocked()

    def clear_trace(self, trace_id: str) -> bool:
        with self._lock:
            if self._state.get("active_trace_id") != trace_id:
                return False
            self._state["active_segment_index"] = None
            self._state["pending_speech_plan"] = None
            self._state["current_expression"] = self._state.get("baseline_expression", "idle")
            self._state["current_motion"] = self._state.get("baseline_motion", "idle")
            self.clear_idle_stage_runtime()
            self._reset_speaking_runtime_unlocked()
            return True

    def _append_event(self, payload: dict[str, Any]) -> None:
        recent = list(self._state.get("recent_events", []))
        recent.append(payload)
        if len(recent) > self._history_limit:
            recent = recent[-self._history_limit :]
        self._state["recent_events"] = recent

    def _reset_speaking_runtime_unlocked(self) -> None:
        self._state["speaking"] = False
        self._state["speech_phase"] = "idle"
        self._state["reactive_level"] = 0.0
        self._state["speaking_signature"] = None
        self._state["speaking_started_at_monotonic"] = None
        self._state["speaking_completed_at_monotonic"] = None
        self._state["speaking_envelope_phase"] = "idle"
        self._state["segment_accent_signature"] = None
        self._state["segment_accent_started_at_monotonic"] = None
        self._state["segment_accent_duration_seconds"] = None
        self._state["segment_retarget_signature"] = None
        self._state["segment_retarget_started_at_monotonic"] = None
        self._state["segment_retarget_duration_seconds"] = None
