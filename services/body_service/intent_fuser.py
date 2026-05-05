from __future__ import annotations

import hashlib
import math
import time
from typing import Any

from services.body_service.models import BodyCommand, BodyProfile, DispatchPlan, IdleFrame, SpeechEvent, clamp


class IntentFuser:
    def __init__(self, profile: BodyProfile) -> None:
        self.profile = profile

    def plan_for_command(self, command: BodyCommand, state: dict[str, Any]) -> DispatchPlan | None:
        if command.command_type == "clear":
            return DispatchPlan(
                operation="clear",
                expression=state.get("baseline_expression") or "idle",
                motion="idle",
                logical_tracking={"head_x": 0.0, "head_y": 0.0, "head_z": 0.0, "eye_x": 0.0, "eye_y": 0.0, "smile": 0.0},
                speaking=False,
                speech_phase="idle",
                reactive_level=0.0,
                metadata={"source": "body_service_command", "clear_speaking_runtime": True},
            )
        if command.command_type == "turn_end":
            return DispatchPlan(
                operation="turn_end",
                expression=state.get("baseline_expression") or "idle",
                motion="idle",
                speaking=False,
                speech_phase="idle",
                reactive_level=0.0,
                metadata={"source": "body_service_command", "clear_speaking_runtime": True},
            )
        if command.command_type == "set_baseline":
            expression_name = command.expression or state.get("baseline_expression") or "idle"
            logical = self._expression_bias(expression_name)
            return DispatchPlan(
                operation="set_baseline",
                expression=expression_name,
                motion=command.motion or "idle",
                logical_tracking=logical,
                speaking=bool(state.get("speaking", False)),
                speech_phase=str(state.get("speech_phase", "idle")),
                reactive_level=float(state.get("reactive_level", 0.0) or 0.0),
                metadata={"source": "body_service_command"},
            )
        if command.sync_to_audio and command.command_type == "speech_plan":
            return None
        expression_name = command.expression or state.get("current_expression") or "idle"
        motion_name = command.motion
        logical = self._expression_bias(expression_name)
        if motion_name:
            logical = _merge_tracking(logical, self._motion_boost(motion_name, command.intensity))
        hotkeys = [hotkey for hotkey in [self._expression_hotkey(expression_name)] if hotkey]
        return DispatchPlan(
            operation="apply_command",
            expression=expression_name,
            motion=motion_name,
            hotkeys=hotkeys,
            logical_tracking=logical,
            speaking=bool(state.get("speaking", False)),
            speech_phase=str(state.get("speech_phase", "idle")),
            reactive_level=float(state.get("reactive_level", 0.0) or 0.0),
            metadata={"source": "body_service_command"},
        )

    def plan_for_speech_event(self, event: SpeechEvent, state: dict[str, Any]) -> DispatchPlan:
        base_expression = str(state.get("baseline_expression") or "idle")
        reactive_expression = self.profile.speech_reactive.speaking_expression or base_expression
        pending = state.get("pending_speech_plan") or {}
        pending_expression = str(pending.get("expression") or reactive_expression)
        pending_motion = str(pending.get("motion") or "") or None
        segment_ratio = _segment_ratio(event)
        if event.action == "segment_start":
            started_at = time.monotonic()
            signature, reused_turn_signature = self._turn_signature_for_event(event, state)
            accent_signature = self._build_segment_accent_signature(event=event, speaking_signature=signature)
            retarget_signature = self._build_segment_retarget_signature(event=event, speaking_signature=signature)
            logical = self._expression_bias(pending_expression)
            logical = _merge_tracking(logical, self._motion_boost(pending_motion, 0.75))
            hotkeys = [hotkey for hotkey in [self._expression_hotkey(pending_expression)] if hotkey]
            if not pending_motion:
                cycle_motion = self._cycle_motion(event.segment_index)
                hotkey = self._motion_hotkey(cycle_motion)
                if hotkey:
                    hotkeys.append(hotkey)
                    pending_motion = cycle_motion
                    logical = _merge_tracking(logical, self._motion_boost(cycle_motion, 0.55))
            return DispatchPlan(
                operation="apply_presence",
                expression=pending_expression,
                motion=pending_motion,
                hotkeys=hotkeys,
                logical_tracking=logical,
                speaking=True,
                speech_phase="speaking",
                reactive_level=max(0.54, float(signature.get("onset_emphasis", 0.68)) * 0.78),
                metadata={
                    "source": "body_service_speech_event",
                    "speaking_state": {
                        "signature": signature,
                        "started_at_monotonic": None if reused_turn_signature else started_at,
                        "completed_at_monotonic": None,
                        "envelope_phase": "speaking",
                        "continued_turn": reused_turn_signature,
                    },
                    "segment_accent_state": {
                        "signature": accent_signature,
                        "started_at_monotonic": started_at,
                        "duration_seconds": float(accent_signature.get("duration_seconds", 0.0) or 0.0),
                    },
                    "segment_retarget_state": {
                        "signature": retarget_signature,
                        "started_at_monotonic": started_at,
                        "duration_seconds": float(retarget_signature.get("duration_seconds", 0.0) or 0.0),
                    },
                },
            )
        if event.action == "segment_progress":
            signature = self._signature_from_state(state) or self._build_speaking_signature(event)
            logical = self._reactive_tracking(
                event=event,
                state=state,
                expression_name=str(state.get("current_expression") or reactive_expression),
                signature=signature,
                segment_ratio=segment_ratio,
            )
            return DispatchPlan(
                operation="apply_reactive_state",
                expression=str(state.get("current_expression") or reactive_expression),
                motion=str(state.get("current_motion") or "idle"),
                logical_tracking=logical,
                speaking=True,
                speech_phase="speaking",
                reactive_level=max(0.35, segment_ratio),
                metadata={"source": "body_service_speech_event"},
            )
        if event.action == "segment_complete":
            signature = self._signature_from_state(state) or self._build_speaking_signature(event)
            settle_strength = float(signature.get("settle_strength", 0.42) or 0.42)
            logical = self._expression_bias(str(state.get("current_expression") or reactive_expression))
            return DispatchPlan(
                operation="segment_complete",
                expression=str(state.get("current_expression") or reactive_expression),
                motion="idle",
                logical_tracking=logical,
                speaking=False,
                speech_phase="bridged_pause",
                reactive_level=max(settle_strength * 0.82, 0.28),
                metadata={
                    "source": "body_service_speech_event",
                    "speaking_state": {
                        "completed_at_monotonic": time.monotonic(),
                        "envelope_phase": "cooldown",
                    },
                    "clear_segment_accent_runtime": True,
                    "clear_segment_retarget_runtime": True,
                },
            )
        if event.action in {"turn_end", "clear"}:
            return DispatchPlan(
                operation=event.action,
                expression=base_expression,
                motion="idle",
                logical_tracking=self._expression_bias(base_expression),
                speaking=False,
                speech_phase="idle",
                reactive_level=0.0,
                metadata={
                    "source": "body_service_speech_event",
                    "clear_speaking_runtime": True,
                    "clear_segment_accent_runtime": True,
                    "clear_segment_retarget_runtime": True,
                },
            )
        return DispatchPlan(operation="noop", metadata={"source": "body_service_speech_event"})

    def build_idle_frame(self, state: dict[str, Any], *, now: float | None = None) -> IdleFrame:
        timestamp = now if now is not None else time.monotonic()
        expression_name = str(state.get("current_expression") or state.get("baseline_expression") or "idle")
        expression_bias = self._expression_bias(expression_name)
        signature = self._signature_from_state(state)
        envelope_phase, envelope, elapsed_seconds, should_clear = self._speaking_envelope(state, now=timestamp, signature=signature)
        stage_active = isinstance(state.get("idle_stage_signature"), dict) and str(state.get("idle_stage_phase", "idle")) == "active"
        base_dampen = 0.68 if stage_active else 1.0
        perf_gain = self.profile.idle.performance_bounce_gain
        loop_gain = self.profile.idle.performance_loop_gain
        base_intensity = self.profile.idle.base_intensity * (
            1.0 + envelope * max(self.profile.idle.speaking_boost - 1.0, 0.0)
        )
        if stage_active:
            perf_gain *= 0.58
            loop_gain *= 0.54
            base_intensity *= base_dampen

        head_x = (
            math.sin(timestamp * 0.42) * self.profile.idle.head_x_range * base_intensity
            + math.sin(timestamp * 0.18 + 1.6) * self.profile.idle.head_x_range * base_intensity * 0.34
            + math.sin(timestamp * 0.09 + 0.8) * self.profile.idle.head_x_range * base_intensity * 0.2
            + math.sin(timestamp * 0.24 + 0.3) * self.profile.idle.performance_head_x_range * 0.22 * perf_gain
        )
        head_y = (
            math.sin(timestamp * 0.28 + 1.1) * self.profile.idle.head_y_range * base_intensity
            + math.sin(timestamp * 0.14 + 0.4) * self.profile.idle.head_y_range * base_intensity * 0.26
            + abs(math.sin(timestamp * 0.22 + 0.2)) * self.profile.idle.head_y_range * base_intensity * 0.22
            + abs(math.sin(timestamp * 0.31 + 0.6)) * self.profile.idle.performance_head_y_range * 0.24 * perf_gain
        )
        head_z = (
            math.sin(timestamp * 0.36 + 2.4) * self.profile.idle.head_z_range * base_intensity
            + math.cos(timestamp * 0.16 + 0.9) * self.profile.idle.head_z_range * base_intensity * 0.28
            + math.sin(timestamp * 0.12 + 1.9) * self.profile.idle.head_z_range * base_intensity * 0.24
            + math.cos(timestamp * 0.27 + 0.5) * self.profile.idle.performance_head_z_range * 0.2 * perf_gain
        )
        smile = self.profile.idle.smile_floor + ((math.sin(timestamp * 0.28) + 1.0) / 2.0) * self.profile.idle.smile_range
        breath = self.profile.idle.breath_floor + ((math.sin(timestamp * 1.4) + 1.0) / 2.0) * self.profile.idle.breath_range
        breath_ratio = 0.0 if self.profile.idle.breath_range <= 0 else clamp(
            (breath - self.profile.idle.breath_floor) / self.profile.idle.breath_range,
            0.0,
            1.0,
        )
        head_y += (breath_ratio - 0.5) * 2.0 * self.profile.idle.breath_head_y_range
        head_z += math.sin(timestamp * 0.62 + 0.6) * self.profile.idle.breath_head_z_range
        loop_wave = math.sin(timestamp * 0.27 + 0.8) * loop_gain
        loop_wave_2 = math.cos(timestamp * 0.33 + 1.7) * loop_gain
        head_x += loop_wave * self.profile.idle.performance_head_x_range * 0.12
        head_y += abs(loop_wave_2) * self.profile.idle.performance_head_y_range * 0.18
        head_z += loop_wave * self.profile.idle.performance_head_z_range * 0.14
        orbit_phase = timestamp * 0.19
        orbit_x = math.sin(orbit_phase) * self.profile.idle.performance_head_x_range * 0.52 * perf_gain
        orbit_y = abs(math.sin(orbit_phase * 1.18 + 0.5)) * self.profile.idle.performance_head_y_range * 0.24 * perf_gain
        orbit_z = math.cos(orbit_phase) * self.profile.idle.performance_head_z_range * 0.48 * perf_gain
        head_x += orbit_x
        head_y += orbit_y
        head_z += orbit_z
        eye_x = (
            math.sin(timestamp * 0.14 + 0.9) * self.profile.idle.eye_x_range * (0.72 if stage_active else 1.0)
            + math.sin(timestamp * 0.24 + 0.6) * self.profile.idle.performance_eye_x_range * 0.18 * (0.56 if stage_active else 1.0)
        )
        eye_y = (
            math.sin(timestamp * 0.11 + 2.1) * self.profile.idle.eye_y_range * (0.72 if stage_active else 1.0)
            + math.cos(timestamp * 0.19 + 1.4) * self.profile.idle.performance_eye_y_range * 0.14 * (0.56 if stage_active else 1.0)
        )

        speaking_boost = self._speaking_tracking(
            signature=signature,
            elapsed_seconds=elapsed_seconds,
            envelope=envelope,
            phase=envelope_phase,
        )
        accent = self._segment_accent_tracking(state=state, timestamp=timestamp, envelope=envelope)
        retarget = self._segment_retarget_tracking(state=state, timestamp=timestamp, envelope=envelope)
        idle_wander = self._idle_wander_tracking(state=state, timestamp=timestamp, envelope=envelope)
        idle_attention = self._idle_attention_tracking(state=state, timestamp=timestamp, envelope=envelope)
        idle_glance = self._idle_glance_tracking(state=state, timestamp=timestamp, envelope=envelope)
        idle_stage = self._idle_stage_tracking(state=state, timestamp=timestamp, envelope=envelope)
        components = {
            "idle_base": {
                "head_x": round(head_x, 4),
                "head_y": round(head_y, 4),
                "head_z": round(head_z, 4),
                "eye_x": round(eye_x, 4),
                "eye_y": round(eye_y, 4),
                "smile": round(smile, 4),
                "breath": round(breath, 4),
            },
            "expression_bias": {key: round(float(value), 4) for key, value in expression_bias.items()},
            "speaking_boost": {key: round(float(value), 4) for key, value in speaking_boost.items()},
            "segment_accent": {key: round(float(value), 4) for key, value in accent.items()},
            "segment_retarget": {key: round(float(value), 4) for key, value in retarget.items()},
            "idle_wander": {key: round(float(value), 4) for key, value in idle_wander.items()},
            "idle_attention": {key: round(float(value), 4) for key, value in idle_attention.items()},
            "idle_glance": {key: round(float(value), 4) for key, value in idle_glance.items()},
            "idle_stage": {key: round(float(value), 4) for key, value in idle_stage.items()},
            "signature_id": str(signature.get("signature_id", "")) if signature else "",
            "elapsed_seconds": round(elapsed_seconds, 4),
        }

        summary = {
            "head_x": round(
                head_x
                + float(expression_bias.get("head_x", 0.0))
                + float(speaking_boost.get("head_x", 0.0))
                + float(accent.get("head_x", 0.0))
                + float(retarget.get("head_x", 0.0))
                + float(idle_wander.get("head_x", 0.0))
                + float(idle_attention.get("head_x", 0.0))
                + float(idle_glance.get("head_x", 0.0))
                + float(idle_stage.get("head_x", 0.0)),
                4,
            ),
            "head_y": round(
                head_y
                + float(expression_bias.get("head_y", 0.0))
                + float(speaking_boost.get("head_y", 0.0))
                + float(accent.get("head_y", 0.0))
                + float(retarget.get("head_y", 0.0))
                + float(idle_wander.get("head_y", 0.0))
                + float(idle_attention.get("head_y", 0.0))
                + float(idle_glance.get("head_y", 0.0))
                + float(idle_stage.get("head_y", 0.0)),
                4,
            ),
            "head_z": round(
                head_z
                + float(expression_bias.get("head_z", 0.0))
                + float(speaking_boost.get("head_z", 0.0))
                + float(accent.get("head_z", 0.0))
                + float(retarget.get("head_z", 0.0))
                + float(idle_wander.get("head_z", 0.0))
                + float(idle_attention.get("head_z", 0.0))
                + float(idle_glance.get("head_z", 0.0))
                + float(idle_stage.get("head_z", 0.0)),
                4,
            ),
            "eye_x": round(
                eye_x
                + float(expression_bias.get("eye_x", 0.0))
                + float(speaking_boost.get("eye_x", 0.0))
                + float(accent.get("eye_x", 0.0))
                + float(retarget.get("eye_x", 0.0))
                + float(idle_wander.get("eye_x", 0.0))
                + float(idle_attention.get("eye_x", 0.0))
                + float(idle_glance.get("eye_x", 0.0))
                + float(idle_stage.get("eye_x", 0.0)),
                4,
            ),
            "eye_y": round(
                eye_y
                + float(expression_bias.get("eye_y", 0.0))
                + float(speaking_boost.get("eye_y", 0.0))
                + float(accent.get("eye_y", 0.0))
                + float(retarget.get("eye_y", 0.0))
                + float(idle_wander.get("eye_y", 0.0))
                + float(idle_attention.get("eye_y", 0.0))
                + float(idle_glance.get("eye_y", 0.0))
                + float(idle_stage.get("eye_y", 0.0)),
                4,
            ),
            "smile": round(
                clamp(
                    smile
                    + float(expression_bias.get("smile", 0.0))
                    + float(speaking_boost.get("smile", 0.0))
                    + float(accent.get("smile", 0.0))
                    + float(idle_stage.get("smile", 0.0)),
                    0.0,
                    1.0,
                ),
                4,
            ),
            "breath": round(breath, 4),
        }
        logical = {key: value for key, value in summary.items() if key != "breath"}
        return IdleFrame(
            logical_tracking=logical,
            summary=summary,
            speech_phase=envelope_phase,
            reactive_level=round(envelope, 4),
            speaking_signature=dict(signature) if signature is not None else None,
            envelope_summary={
                "phase": envelope_phase,
                "envelope": round(envelope, 4),
                "should_clear": should_clear,
                "components": components,
            },
            stage_summary={
                "name": str(state.get("idle_stage_name", "")),
                "phase": str(state.get("idle_stage_phase", "idle")),
                "expression": str(state.get("idle_stage_expression", "")),
                "motion": str(state.get("idle_stage_motion", "")),
                "hotkey": str(state.get("idle_stage_hotkey", "")),
                "signature_id": str((state.get("idle_stage_signature") or {}).get("signature_id", "")) if isinstance(state.get("idle_stage_signature"), dict) else "",
            },
            clear_speaking_signature=should_clear,
        )

    def translate_tracking(self, logical: dict[str, float]) -> dict[str, float]:
        translated: dict[str, float] = {}
        for logical_name, value in logical.items():
            tracking_id = self.profile.tracking_inputs.get(logical_name)
            if tracking_id:
                translated[tracking_id] = round(float(value), 4)
        return translated

    def build_idle_wander_signature(self, *, body_id: str, counter: int) -> dict[str, Any]:
        seed_text = f"{body_id}|idle|wander|{counter}"
        units = _stable_units(seed_text, count=7)
        idle = self.profile.idle
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "anchor_head_x": _range(-idle.wander_head_x_range, idle.wander_head_x_range, units[0]),
            "anchor_head_y": _range(-idle.wander_head_y_range, idle.wander_head_y_range, units[1]),
            "anchor_head_z": _range(-idle.wander_head_z_range, idle.wander_head_z_range, units[2]),
            "anchor_eye_x": _range(-idle.wander_eye_x_range, idle.wander_eye_x_range, units[3]),
            "anchor_eye_y": _range(-idle.wander_eye_y_range, idle.wander_eye_y_range, units[4]),
            "gaze_phase": _range(0.0, math.tau, units[5]),
            "duration_seconds": _range(idle.wander_duration_min_s, idle.wander_duration_max_s, units[6]),
        }

    def build_idle_attention_signature(
        self,
        *,
        body_id: str,
        counter: int,
        last_target_head_x: float = 0.0,
        last_target_head_y: float = 0.0,
        last_target_head_z: float = 0.0,
    ) -> dict[str, Any]:
        seed_text = f"{body_id}|idle|attention|{counter}"
        units = _stable_units(seed_text, count=11)
        idle = self.profile.idle
        target_eye_x = _signed_span(idle.attention_eye_x_range, direction_unit=units[0], magnitude_unit=units[1], min_ratio=0.62)
        target_eye_y = _signed_span(idle.attention_eye_y_range, direction_unit=units[2], magnitude_unit=units[3], min_ratio=0.38)
        duration_seconds = _range(idle.attention_duration_min_s, idle.attention_duration_max_s, units[2])
        hold_ratio = _range(idle.attention_hold_ratio * 0.94, min(0.78, idle.attention_hold_ratio * 1.28), units[4])
        head_x = clamp(target_eye_x * _range(9.8, 14.2, units[5]), -idle.attention_head_x_range, idle.attention_head_x_range)
        head_y = clamp(target_eye_y * _range(6.0, 8.4, units[6]), -idle.attention_head_y_range, idle.attention_head_y_range)
        head_z = clamp(target_eye_x * _range(4.8, 6.6, units[7]), -idle.attention_head_z_range, idle.attention_head_z_range)
        if last_target_head_x != 0.0:
            head_x = _blend_idle_heading(last_target_head_x, head_x, max_delta=idle.attention_head_x_range * 0.72)
        if last_target_head_y != 0.0:
            head_y = _blend_idle_heading(last_target_head_y, head_y, max_delta=idle.attention_head_y_range * 0.68)
        if last_target_head_z != 0.0:
            head_z = _blend_idle_heading(last_target_head_z, head_z, max_delta=idle.attention_head_z_range * 0.7)
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "target_eye_x": target_eye_x,
            "target_eye_y": target_eye_y,
            "head_x": head_x,
            "head_y": head_y,
            "head_z": head_z,
            "duration_seconds": duration_seconds,
            "hold_ratio": hold_ratio,
            "eye_lead_ratio": idle.attention_eye_lead_ratio,
            "return_softness": idle.attention_return_softness,
            "head_follow_gain": idle.attention_head_follow_gain,
            "body_follow_gain": idle.attention_body_follow_gain,
            "look_bias": _range(-1.0, 1.0, units[8]),
            "micro_phase": _range(0.0, math.tau, units[9]),
            "bounce_bias": _range(0.0, 1.0, units[10]),
        }

    def build_idle_glance_signature(self, *, body_id: str, counter: int) -> dict[str, Any]:
        seed_text = f"{body_id}|idle|glance|{counter}"
        units = _stable_units(seed_text, count=12)
        idle = self.profile.idle
        duration_seconds = _range(idle.glance_duration_min_s, idle.glance_duration_max_s, units[5])
        hold_ratio = _range(idle.glance_hold_ratio * 0.9, min(0.58, idle.glance_hold_ratio * 1.45), units[6])
        target_eye_x = _signed_span(idle.glance_eye_x_range, direction_unit=units[3], magnitude_unit=units[4], min_ratio=0.58)
        target_eye_y = _signed_span(idle.glance_eye_y_range, direction_unit=units[10], magnitude_unit=units[11], min_ratio=0.34)
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "target_eye_x": target_eye_x,
            "target_eye_y": target_eye_y,
            "glance_head_x": clamp(target_eye_x * _range(5.2, 8.4, units[0]), -idle.glance_head_x_range, idle.glance_head_x_range),
            "glance_head_y": clamp(target_eye_y * _range(2.8, 4.4, units[1]), -idle.glance_head_y_range, idle.glance_head_y_range),
            "glance_head_z": clamp(target_eye_x * _range(2.2, 3.6, units[2]), -idle.glance_head_z_range, idle.glance_head_z_range),
            "glance_eye_x": target_eye_x,
            "glance_eye_y": target_eye_y,
            "duration_seconds": duration_seconds,
            "hold_ratio": hold_ratio,
            "return_softness": _range(idle.glance_return_softness * 0.82, min(1.0, idle.glance_return_softness * 1.15), units[7]),
            "eye_lead_ratio": _range(0.14, 0.26, units[8]),
            "head_follow_gain": _range(1.35, 1.72, units[9]),
        }

    def build_idle_stage_signature(self, *, body_id: str, counter: int, stage_name: str) -> dict[str, Any]:
        spec = self.profile.idle_stages.get(stage_name)
        if spec is None:
            return {}
        seed_text = f"{body_id}|idle|stage|{stage_name}|{counter}"
        units = _stable_units(seed_text, count=20)
        duration_seconds = _range(spec.duration_min_s, max(spec.duration_min_s, spec.duration_max_s), units[0])
        hold_ratio = clamp(spec.hold_ratio, 0.0, 0.72)
        switch_side = units[1] <= clamp(spec.switch_chance, 0.0, 1.0)
        yaw_direction = -1.0 if switch_side else 1.0
        pitch_direction = _direction(units[2])
        roll_direction = _direction(units[3])
        motion_name = spec.motion if spec.motion and units[4] <= clamp(spec.hotkey_probability, 0.0, 1.0) else ""
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "stage_name": stage_name,
            "pattern": spec.pattern,
            "duration_seconds": duration_seconds,
            "hold_ratio": hold_ratio,
            "entry_ratio": _range(0.08, 0.18, units[15]),
            "release_ratio": _range(0.14, 0.24, units[16]),
            "yaw_direction": yaw_direction,
            "pitch_direction": pitch_direction,
            "roll_direction": roll_direction,
            "anchor_head_x": _range(spec.head_x_range * 0.42, spec.head_x_range * 0.72, units[5]),
            "anchor_head_y": _range(spec.head_y_range * 0.24, spec.head_y_range * 0.52, units[6]),
            "anchor_head_z": _range(spec.head_z_range * 0.28, spec.head_z_range * 0.58, units[7]),
            "anchor_eye_x": _range(spec.eye_x_range * 0.36, spec.eye_x_range * 0.66, units[8]),
            "anchor_eye_y": _range(spec.eye_y_range * 0.24, spec.eye_y_range * 0.52, units[9]),
            "burst_head_x": _range(spec.head_x_range * 0.22, spec.head_x_range * 0.46, units[10]),
            "burst_head_y": _range(spec.head_y_range * 0.18, spec.head_y_range * 0.44, units[11]),
            "burst_head_z": _range(spec.head_z_range * 0.18, spec.head_z_range * 0.42, units[12]),
            "burst_eye_x": _range(spec.eye_x_range * 0.24, spec.eye_x_range * 0.46, units[13]),
            "burst_eye_y": _range(spec.eye_y_range * 0.18, spec.eye_y_range * 0.42, units[14]),
            "smile_boost": max(0.0, spec.smile_boost),
            "bounce_gain": max(0.0, spec.bounce_gain),
            "loop_gain": max(0.0, spec.loop_gain),
            "eye_lead_ratio": clamp(spec.eye_lead_ratio, 0.0, 0.9),
            "head_follow_ratio": clamp(spec.head_follow_ratio, 0.0, 0.96),
            "body_follow_ratio": clamp(spec.body_follow_ratio, 0.0, 0.98),
            "return_softness": clamp(spec.return_softness, 0.2, 1.4),
            "tracking_gain": max(0.0, spec.tracking_gain),
            "expression": spec.expression,
            "motion": motion_name,
            "motion_intensity": max(0.0, spec.motion_intensity),
            "burst_start_ratio": _range(0.18, 0.42, units[17]),
            "burst_duration_ratio": _range(0.1, 0.22, units[18]),
            "correction_start_ratio": _range(0.52, 0.78, units[19]),
            "correction_duration_ratio": _range(0.08, 0.16, units[3]),
            "stage_phase": _range(0.0, math.tau, units[10]),
            "micro_phase": _range(0.0, math.tau, units[11]),
            "peek_bias": _range(-1.0, 1.0, units[12]),
            "bounce_bias": _range(0.0, 1.0, units[13]),
            "loop_bias": _range(-1.0, 1.0, units[14]),
            "stage_weight": max(0.0, spec.weight),
        }

    def next_idle_stage_delay_seconds(self, *, counter: int) -> float:
        unit = _stable_units(f"{self.profile.body_id}|idle|stage-delay|{counter}", count=1)[0]
        runtime = self.profile.idle_stage_runtime
        return _range(runtime.cooldown_min_s, max(runtime.cooldown_min_s, runtime.cooldown_max_s), unit)

    def choose_idle_stage_name(self, *, counter: int) -> str:
        entries = [(name, spec) for name, spec in self.profile.idle_stages.items() if spec.weight > 0]
        if not entries:
            return ""
        total = sum(spec.weight for _, spec in entries)
        if total <= 0:
            return entries[0][0]
        unit = _stable_units(f"{self.profile.body_id}|idle|stage-pick|{counter}", count=1)[0]
        target = unit * total
        cursor = 0.0
        for name, spec in entries:
            cursor += spec.weight
            if target <= cursor:
                return name
        return entries[-1][0]

    def next_idle_glance_delay_seconds(self, *, counter: int) -> float:
        unit = _stable_units(f"{self.profile.body_id}|idle|glance-delay|{counter}", count=1)[0]
        return _range(1.05, 2.35, unit)

    def _reactive_tracking(
        self,
        *,
        event: SpeechEvent,
        state: dict[str, Any],
        expression_name: str,
        signature: dict[str, Any],
        segment_ratio: float,
    ) -> dict[str, float]:
        logical = self._expression_bias(expression_name)
        elapsed_seconds = self._signature_elapsed_seconds(state, now=time.monotonic())
        logical = _merge_tracking(
            logical,
            self._speaking_tracking(
                signature=signature,
                elapsed_seconds=max(elapsed_seconds, segment_ratio * 0.8),
                envelope=max(0.45, segment_ratio),
                phase="speaking",
            ),
        )
        accent_signature = state.get("segment_accent_signature")
        accent_started_at = float(state.get("segment_accent_started_at_monotonic") or 0.0)
        accent_duration = float(state.get("segment_accent_duration_seconds") or 0.0)
        if isinstance(accent_signature, dict) and accent_started_at > 0 and accent_duration > 0:
            logical = _merge_tracking(
                logical,
                self._accent_tracking(
                    signature=accent_signature,
                    elapsed_seconds=max(time.monotonic() - accent_started_at, 0.0),
                    duration_seconds=accent_duration,
                ),
            )
        retarget_signature = state.get("segment_retarget_signature")
        retarget_started_at = float(state.get("segment_retarget_started_at_monotonic") or 0.0)
        retarget_duration = float(state.get("segment_retarget_duration_seconds") or 0.0)
        if isinstance(retarget_signature, dict) and retarget_started_at > 0 and retarget_duration > 0:
            logical = _merge_tracking(
                logical,
                self._retarget_tracking(
                    signature=retarget_signature,
                    elapsed_seconds=max(time.monotonic() - retarget_started_at, 0.0),
                    duration_seconds=retarget_duration,
                ),
            )
        logical["smile"] = clamp(float(logical.get("smile", 0.08)) + segment_ratio * 0.05, 0.0, 1.0)
        logical["head_y"] = float(logical.get("head_y", 0.0)) + math.sin((event.segment_index or 1) * 0.4) * 0.12
        return logical

    def _expression_bias(self, expression_name: str | None) -> dict[str, float]:
        if not expression_name:
            return {}
        spec = self.profile.expressions.get(expression_name)
        return dict(spec.tracking_bias) if spec is not None else {}

    def _motion_boost(self, motion_name: str | None, intensity: float) -> dict[str, float]:
        if not motion_name:
            return {}
        spec = self.profile.motions.get(motion_name)
        if spec is None:
            return {}
        return {key: value * max(0.0, intensity) for key, value in spec.tracking_boost.items()}

    def _expression_hotkey(self, expression_name: str | None) -> str:
        if not expression_name:
            return ""
        spec = self.profile.expressions.get(expression_name)
        return spec.hotkey if spec is not None else ""

    def _motion_hotkey(self, motion_name: str | None) -> str:
        if not motion_name:
            return ""
        spec = self.profile.motions.get(motion_name)
        return spec.hotkey if spec is not None else ""

    def _cycle_motion(self, segment_index: int | None) -> str | None:
        cycle = self.profile.speech_reactive.motion_cycle
        if not cycle:
            return None
        index = max(0, (segment_index or 1) - 1) % len(cycle)
        return cycle[index]

    def _build_speaking_signature(self, event: SpeechEvent) -> dict[str, Any]:
        seed_text = f"{event.trace_id}|{event.generation_id}|{event.segment_index or 0}|{event.text.strip()}"
        units = _stable_units(seed_text, count=12)
        reactive = self.profile.speech_reactive
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "seed_input": seed_text,
            "yaw_direction": _direction(units[0]),
            "roll_direction": _direction(units[1]),
            "pitch_scale": _range(0.72, 1.24, units[2]),
            "signature_scale": _range(reactive.signature_scale_min, reactive.signature_scale_max, units[3]),
            "sway_rate_hz": _range(reactive.sway_rate_min_hz, reactive.sway_rate_max_hz, units[4]),
            "sway_phase": _range(0.0, math.tau, units[5]),
            "onset_emphasis": _range(reactive.onset_emphasis_min, reactive.onset_emphasis_max, units[6]),
            "settle_strength": _range(reactive.settle_strength_min, reactive.settle_strength_max, units[7]),
            "smile_boost": _range(reactive.smile_boost_min, reactive.smile_boost_max, units[1]),
            "glance_rate_hz": _range(reactive.glance_rate_min_hz, reactive.glance_rate_max_hz, units[8]),
            "glance_bias_y": _range(-1.0, 1.0, units[9]),
            "figure8_mix": _range(reactive.figure8_mix_min, reactive.figure8_mix_max, units[10]),
            "drift_rate_hz": _range(0.09, 0.24, units[11]),
        }

    def _build_segment_accent_signature(self, *, event: SpeechEvent, speaking_signature: dict[str, Any]) -> dict[str, Any]:
        reactive = self.profile.speech_reactive
        seed_text = f"{event.trace_id}|{event.generation_id}|{event.segment_index or 0}|accent|{event.text.strip()}"
        units = _stable_units(seed_text, count=12)
        estimated_duration_ms = max(
            reactive.segment_duration_floor_ms,
            int(len(event.text.strip()) * reactive.segment_duration_per_char_ms),
        )
        duration_seconds = estimated_duration_ms / 1000.0
        accent_window_ratio = _range(reactive.accent_window_ratio_min, reactive.accent_window_ratio_max, units[0])
        accent_duration_ratio = _range(reactive.accent_duration_ratio_min, reactive.accent_duration_ratio_max, units[1])
        accent_hold_ratio = _range(reactive.accent_hold_ratio_min, reactive.accent_hold_ratio_max, units[2])
        segment_start_seconds = min(duration_seconds * accent_window_ratio, 0.18)
        accent_duration_seconds = max(0.16, duration_seconds * accent_duration_ratio)
        switch_side = units[3] <= clamp(reactive.accent_switch_chance, 0.0, 1.0)
        base_yaw_direction = float(speaking_signature.get("yaw_direction", 1.0) or 1.0)
        accent_yaw_direction = -base_yaw_direction if switch_side else base_yaw_direction
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "segment_start_seconds": segment_start_seconds,
            "duration_seconds": accent_duration_seconds,
            "hold_ratio": accent_hold_ratio,
            "eye_lead_ratio": reactive.accent_eye_lead_ratio,
            "head_follow_ratio": reactive.accent_head_follow_ratio,
            "body_follow_ratio": reactive.accent_body_follow_ratio,
            "yaw_direction": accent_yaw_direction,
            "pitch_direction": _direction(units[4]),
            "roll_direction": _direction(units[5]),
            "yaw_amount": _range(reactive.accent_yaw_range * 0.72, reactive.accent_yaw_range, units[6]),
            "pitch_amount": _range(reactive.accent_pitch_range * 0.68, reactive.accent_pitch_range, units[7]),
            "roll_amount": _range(reactive.accent_roll_range * 0.7, reactive.accent_roll_range, units[8]),
            "eye_x_amount": _range(reactive.accent_eye_x_range * 0.72, reactive.accent_eye_x_range, units[9]),
            "eye_y_amount": _range(reactive.accent_eye_y_range * 0.66, reactive.accent_eye_y_range, units[10]),
            "figure8_mix": _range(reactive.figure8_mix_min, reactive.figure8_mix_max, units[11]),
        }

    def _build_segment_retarget_signature(self, *, event: SpeechEvent, speaking_signature: dict[str, Any]) -> dict[str, Any]:
        reactive = self.profile.speech_reactive
        seed_text = f"{event.trace_id}|{event.generation_id}|{event.segment_index or 0}|retarget|{event.text.strip()}"
        units = _stable_units(seed_text, count=11)
        estimated_duration_ms = max(
            reactive.segment_duration_floor_ms,
            int(len(event.text.strip()) * reactive.segment_duration_per_char_ms),
        )
        duration_seconds = estimated_duration_ms / 1000.0
        retarget_window_ratio = _range(reactive.retarget_window_ratio_min, reactive.retarget_window_ratio_max, units[0])
        retarget_duration_ratio = _range(reactive.retarget_duration_ratio_min, reactive.retarget_duration_ratio_max, units[1])
        retarget_hold_ratio = _range(reactive.retarget_hold_ratio_min, reactive.retarget_hold_ratio_max, units[2])
        segment_start_seconds = duration_seconds * retarget_window_ratio
        retarget_duration_seconds = max(0.18, duration_seconds * retarget_duration_ratio)
        switch_side = units[3] <= clamp(reactive.retarget_switch_chance, 0.0, 1.0)
        base_yaw_direction = float(speaking_signature.get("yaw_direction", 1.0) or 1.0)
        retarget_yaw_direction = -base_yaw_direction if switch_side else base_yaw_direction
        target_eye_x = retarget_yaw_direction * _range(reactive.retarget_eye_x_range * 0.7, reactive.retarget_eye_x_range, units[9])
        target_eye_y = _direction(units[4]) * _range(reactive.retarget_eye_y_range * 0.6, reactive.retarget_eye_y_range, units[10])
        return {
            "signature_id": hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12],
            "segment_start_seconds": segment_start_seconds,
            "duration_seconds": retarget_duration_seconds,
            "hold_ratio": retarget_hold_ratio,
            "eye_lead_ratio": reactive.retarget_eye_lead_ratio,
            "head_follow_ratio": reactive.retarget_head_follow_ratio,
            "body_follow_ratio": reactive.retarget_body_follow_ratio,
            "yaw_direction": retarget_yaw_direction,
            "pitch_direction": _direction(units[4]),
            "roll_direction": _direction(units[5]),
            "target_eye_x": target_eye_x,
            "target_eye_y": target_eye_y,
            "yaw_amount": _range(reactive.retarget_yaw_range * 0.72, reactive.retarget_yaw_range, units[6]),
            "pitch_amount": _range(reactive.retarget_pitch_range * 0.7, reactive.retarget_pitch_range, units[7]),
            "roll_amount": _range(reactive.retarget_roll_range * 0.72, reactive.retarget_roll_range, units[8]),
            "eye_x_amount": target_eye_x,
            "eye_y_amount": target_eye_y,
        }

    def _signature_from_state(self, state: dict[str, Any]) -> dict[str, Any] | None:
        signature = state.get("speaking_signature")
        return dict(signature) if isinstance(signature, dict) and signature else None

    def _turn_signature_for_event(self, event: SpeechEvent, state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        current = self._signature_from_state(state)
        if (
            current is not None
            and str(state.get("active_trace_id", "")) == event.trace_id
            and int(state.get("active_generation_id", 1) or 1) == event.generation_id
        ):
            return current, True
        return self._build_speaking_signature(event), False

    def _speaking_envelope(
        self,
        state: dict[str, Any],
        *,
        now: float,
        signature: dict[str, Any] | None,
    ) -> tuple[str, float, float, bool]:
        if not signature:
            return "idle", 0.0, 0.0, False
        started_at = float(state.get("speaking_started_at_monotonic") or 0.0)
        if started_at <= 0:
            return "idle", 0.0, 0.0, True
        reactive = self.profile.speech_reactive
        phase = str(state.get("speaking_envelope_phase") or state.get("speech_phase") or "idle")
        elapsed_seconds = max(now - started_at, 0.0)
        if phase == "speaking":
            rise = min(elapsed_seconds / 0.26, 1.0)
            envelope = 0.56 + 0.44 * rise
            return "speaking", clamp(envelope, 0.0, 1.0), elapsed_seconds, False
        if phase == "bridged_pause":
            completed_at = float(state.get("speaking_completed_at_monotonic") or now)
            pause_elapsed = max(now - completed_at, 0.0)
            bridge_seconds = max(reactive.segment_bridge_hold_ms / 1000.0, 0.0)
            settle_strength = float(signature.get("settle_strength", reactive.settle_strength_min) or reactive.settle_strength_min)
            if pause_elapsed <= bridge_seconds:
                fade = 1.0 - (pause_elapsed / bridge_seconds) if bridge_seconds > 0 else 0.0
                envelope = max(settle_strength * 0.94, 0.42) * (0.86 + 0.14 * fade)
                return "bridged_pause", clamp(envelope, 0.0, 1.0), elapsed_seconds, False
            phase = "cooldown"
        if phase == "cooldown":
            completed_at = float(state.get("speaking_completed_at_monotonic") or now)
            cooldown_elapsed = max(now - completed_at, 0.0)
            bridge_seconds = max(reactive.segment_bridge_hold_ms / 1000.0, 0.0)
            hold_seconds = max(reactive.segment_complete_hold_ms / 1000.0, 0.0)
            falloff_seconds = max(reactive.cooldown_falloff_ms / 1000.0, 0.05)
            settle_strength = float(signature.get("settle_strength", reactive.settle_strength_min) or reactive.settle_strength_min)
            if cooldown_elapsed > bridge_seconds:
                cooldown_elapsed -= bridge_seconds
            if cooldown_elapsed <= hold_seconds:
                hold_ratio = 1.0 - (cooldown_elapsed / hold_seconds) if hold_seconds > 0 else 0.0
                envelope = settle_strength * (0.84 + 0.16 * hold_ratio)
                return "cooldown", clamp(envelope, 0.0, 1.0), elapsed_seconds, False
            decay_elapsed = cooldown_elapsed - hold_seconds
            decay = 1.0 - (decay_elapsed / falloff_seconds)
            envelope = settle_strength * max(decay, 0.0)
            should_clear = envelope <= 0.01
            return ("idle" if should_clear else "cooldown"), clamp(envelope, 0.0, 1.0), elapsed_seconds, should_clear
        return "idle", 0.0, elapsed_seconds, True

    def _speaking_tracking(
        self,
        *,
        signature: dict[str, Any] | None,
        elapsed_seconds: float,
        envelope: float,
        phase: str,
    ) -> dict[str, float]:
        if not signature or envelope <= 0:
            return {}
        reactive = self.profile.speech_reactive
        yaw_direction = float(signature.get("yaw_direction", 1.0) or 1.0)
        roll_direction = float(signature.get("roll_direction", 1.0) or 1.0)
        pitch_scale = float(signature.get("pitch_scale", 1.0) or 1.0)
        signature_scale = float(signature.get("signature_scale", 1.0) or 1.0)
        sway_rate_hz = float(signature.get("sway_rate_hz", reactive.sway_rate_min_hz) or reactive.sway_rate_min_hz)
        sway_phase = float(signature.get("sway_phase", 0.0) or 0.0)
        onset_emphasis = float(signature.get("onset_emphasis", reactive.onset_emphasis_min) or reactive.onset_emphasis_min)
        settle_strength = float(signature.get("settle_strength", reactive.settle_strength_min) or reactive.settle_strength_min)
        smile_boost = float(signature.get("smile_boost", reactive.smile_boost_min) or reactive.smile_boost_min)
        glance_rate_hz = float(signature.get("glance_rate_hz", reactive.glance_rate_min_hz) or reactive.glance_rate_min_hz)
        glance_bias_y = float(signature.get("glance_bias_y", 0.0) or 0.0)
        figure8_mix = float(signature.get("figure8_mix", reactive.figure8_mix_min) or reactive.figure8_mix_min)
        drift_rate_hz = float(signature.get("drift_rate_hz", 0.12) or 0.12)

        primary_wave = math.sin((elapsed_seconds * sway_rate_hz * math.tau) + sway_phase)
        counter_wave = math.sin((elapsed_seconds * (sway_rate_hz * 0.41 + drift_rate_hz) * math.tau) + (sway_phase * 0.57))
        diagonal_wave = math.cos((elapsed_seconds * (sway_rate_hz * 0.33 + drift_rate_hz * 0.8) * math.tau) + (sway_phase * 0.31))
        drift_wave = math.sin((elapsed_seconds * drift_rate_hz * math.tau) + (sway_phase * 0.24))
        drift_wave_y = math.cos((elapsed_seconds * (drift_rate_hz * 0.78) * math.tau) + (sway_phase * 0.18))
        glance_wave = math.sin((elapsed_seconds * glance_rate_hz * math.tau) + (sway_phase * 0.85))
        glance_wave_soft = math.sin((elapsed_seconds * (glance_rate_hz * 0.36 + 0.05) * math.tau) + (sway_phase * 0.37))
        glance_wave_y = math.cos((elapsed_seconds * (glance_rate_hz * 0.62 + 0.08) * math.tau) + (sway_phase * 0.53))
        onset_pulse = math.exp(-elapsed_seconds * 3.2) * onset_emphasis if phase == "speaking" else 0.0
        settle_bias = settle_strength * envelope if phase == "cooldown" else 0.0
        anchor_blend = min(elapsed_seconds / 0.34, 1.0)
        yaw_anchor = yaw_direction * reactive.speaking_anchor_yaw_range * signature_scale * envelope * anchor_blend
        pitch_anchor = reactive.speaking_anchor_pitch_range * pitch_scale * envelope * anchor_blend * 0.86
        roll_anchor = roll_direction * reactive.speaking_anchor_roll_range * signature_scale * envelope * anchor_blend

        figure8_yaw = counter_wave * reactive.sway_yaw_range * signature_scale * envelope * figure8_mix
        figure8_pitch = diagonal_wave * reactive.sway_pitch_range * envelope * figure8_mix
        figure8_roll = -counter_wave * reactive.sway_roll_range * signature_scale * envelope * figure8_mix * 0.72
        bounce_wave = abs(math.sin((elapsed_seconds * (sway_rate_hz * 0.78 + 0.11) * math.tau) + (sway_phase * 0.41))) - 0.5
        lift_wave = math.sin((elapsed_seconds * (sway_rate_hz * 0.52 + 0.09) * math.tau) + (sway_phase * 0.22))
        body_wave = math.sin((elapsed_seconds * (sway_rate_hz * 0.36 + 0.06) * math.tau) + (sway_phase * 0.17))
        body_wave_cross = math.cos((elapsed_seconds * (sway_rate_hz * 0.27 + 0.04) * math.tau) + (sway_phase * 0.12))

        head_x = (
            yaw_anchor
            + yaw_direction * reactive.speaking_yaw_range * signature_scale * envelope * (0.16 + onset_pulse * 0.46)
            + primary_wave * reactive.sway_yaw_range * signature_scale * envelope
            + drift_wave * reactive.sway_yaw_range * 0.48 * envelope
            + figure8_yaw
            + body_wave * reactive.sway_yaw_range * 0.44 * envelope
            + body_wave_cross * reactive.sway_yaw_range * 0.18 * envelope
        )
        head_y = (
            pitch_anchor
            + reactive.speaking_pitch_range * pitch_scale * envelope * (0.14 + onset_pulse * 0.78)
            + diagonal_wave * reactive.sway_pitch_range * envelope
            + drift_wave_y * reactive.sway_pitch_range * 0.38 * envelope
            + figure8_pitch
            + bounce_wave * reactive.sway_pitch_range * 0.92 * envelope
            + body_wave_cross * reactive.sway_pitch_range * 0.72 * envelope
            + settle_bias * 0.22
        )
        head_z = (
            roll_anchor
            + roll_direction * reactive.speaking_roll_range * signature_scale * envelope * (0.14 + onset_pulse * 0.34)
            + primary_wave * reactive.sway_roll_range * signature_scale * envelope
            + drift_wave * reactive.sway_roll_range * 0.34 * envelope
            + figure8_roll
            + lift_wave * reactive.sway_roll_range * 0.62 * envelope
            + body_wave * reactive.sway_roll_range * 0.86 * envelope
            + body_wave_cross * reactive.sway_roll_range * 0.26 * envelope
            + yaw_direction * settle_bias * 0.12
        )
        smile = clamp(smile_boost * envelope + onset_pulse * 0.018 + settle_bias * 0.01, 0.0, 1.0)
        eye_x = (
            yaw_direction * reactive.speaking_eye_anchor_x_range * envelope * anchor_blend
            + glance_wave * reactive.speaking_eye_x_range * 0.64 * envelope
            + glance_wave_soft * reactive.speaking_eye_x_range * 0.24 * envelope
            - primary_wave * reactive.speaking_eye_x_range * 0.08 * envelope
        )
        eye_y = (
            glance_bias_y * reactive.speaking_eye_anchor_y_range * envelope * anchor_blend
            + glance_wave_y * reactive.speaking_eye_y_range * 0.66 * envelope
            + drift_wave_y * reactive.speaking_eye_y_range * 0.18 * envelope
            + settle_bias * 0.02
        )
        return {
            "head_x": round(head_x, 4),
            "head_y": round(head_y, 4),
            "head_z": round(head_z, 4),
            "eye_x": round(eye_x, 4),
            "eye_y": round(eye_y, 4),
            "smile": round(smile, 4),
        }

    def _settle_tracking(self, *, signature: dict[str, Any], settle_strength: float) -> dict[str, float]:
        yaw_direction = float(signature.get("yaw_direction", 1.0) or 1.0)
        roll_direction = float(signature.get("roll_direction", 1.0) or 1.0)
        return {
            "head_x": round(yaw_direction * 0.34 * settle_strength, 4),
            "head_y": round(0.22 * settle_strength, 4),
            "head_z": round(roll_direction * 0.18 * settle_strength, 4),
            "eye_x": round(yaw_direction * 0.04 * settle_strength, 4),
            "smile": round(min(0.08, 0.02 + settle_strength * 0.035), 4),
        }

    def _signature_elapsed_seconds(self, state: dict[str, Any], *, now: float) -> float:
        started_at = float(state.get("speaking_started_at_monotonic") or 0.0)
        if started_at <= 0:
            return 0.0
        return max(now - started_at, 0.0)

    def _segment_accent_tracking(self, *, state: dict[str, Any], timestamp: float, envelope: float) -> dict[str, float]:
        if envelope <= 0.04:
            return {}
        signature = state.get("segment_accent_signature")
        started_at = float(state.get("segment_accent_started_at_monotonic") or 0.0)
        duration = float(state.get("segment_accent_duration_seconds") or 0.0)
        if not isinstance(signature, dict) or started_at <= 0 or duration <= 0:
            return {}
        elapsed = max(timestamp - started_at, 0.0)
        return self._accent_tracking(signature=signature, elapsed_seconds=elapsed, duration_seconds=duration)

    def _segment_retarget_tracking(self, *, state: dict[str, Any], timestamp: float, envelope: float) -> dict[str, float]:
        if envelope <= 0.04:
            return {}
        signature = state.get("segment_retarget_signature")
        started_at = float(state.get("segment_retarget_started_at_monotonic") or 0.0)
        duration = float(state.get("segment_retarget_duration_seconds") or 0.0)
        if not isinstance(signature, dict) or started_at <= 0 or duration <= 0:
            return {}
        elapsed = max(timestamp - started_at, 0.0)
        return self._retarget_tracking(signature=signature, elapsed_seconds=elapsed, duration_seconds=duration)

    def _accent_tracking(self, *, signature: dict[str, Any], elapsed_seconds: float, duration_seconds: float) -> dict[str, float]:
        if duration_seconds <= 0:
            return {}
        start_offset = float(signature.get("segment_start_seconds", 0.0) or 0.0)
        if elapsed_seconds < start_offset:
            return {}
        active_elapsed = elapsed_seconds - start_offset
        if active_elapsed > duration_seconds:
            return {}
        hold_ratio = clamp(float(signature.get("hold_ratio", 0.16) or 0.16), 0.0, 0.75)
        hold_seconds = max(duration_seconds * hold_ratio, 0.12)
        eye_lead_ratio = clamp(float(signature.get("eye_lead_ratio", 0.4) or 0.4), 0.0, 0.95)
        head_follow_ratio = max(eye_lead_ratio + 0.04, float(signature.get("head_follow_ratio", 0.72) or 0.72))
        body_follow_ratio = max(head_follow_ratio + 0.06, float(signature.get("body_follow_ratio", 1.08) or 1.08))

        eye_env = _impulse_window(active_elapsed, duration_seconds, lead_ratio=eye_lead_ratio, hold_seconds=hold_seconds)
        head_env = _impulse_window(active_elapsed, duration_seconds, lead_ratio=head_follow_ratio, hold_seconds=hold_seconds * 0.9)
        body_env = _impulse_window(active_elapsed, duration_seconds, lead_ratio=body_follow_ratio, hold_seconds=hold_seconds * 0.86)
        figure8_mix = float(signature.get("figure8_mix", 0.12) or 0.12)
        phase = clamp(active_elapsed / max(duration_seconds, 0.001), 0.0, 1.0)
        figure8 = math.sin(phase * math.tau) * math.cos(phase * math.pi)

        yaw_direction = float(signature.get("yaw_direction", 1.0) or 1.0)
        pitch_direction = float(signature.get("pitch_direction", 1.0) or 1.0)
        roll_direction = float(signature.get("roll_direction", 1.0) or 1.0)
        head_x = yaw_direction * float(signature.get("yaw_amount", 0.0) or 0.0) * head_env
        head_y = pitch_direction * float(signature.get("pitch_amount", 0.0) or 0.0) * body_env * 0.64 + figure8 * figure8_mix * 0.22
        head_z = roll_direction * float(signature.get("roll_amount", 0.0) or 0.0) * body_env + figure8 * figure8_mix * 0.46
        eye_x = yaw_direction * float(signature.get("eye_x_amount", 0.0) or 0.0) * eye_env
        eye_y = pitch_direction * float(signature.get("eye_y_amount", 0.0) or 0.0) * eye_env * 0.84
        smile = max(0.0, head_env * 0.015)
        return {
            "head_x": round(head_x, 4),
            "head_y": round(head_y, 4),
            "head_z": round(head_z, 4),
            "eye_x": round(eye_x, 4),
            "eye_y": round(eye_y, 4),
            "smile": round(smile, 4),
        }

    def _retarget_tracking(self, *, signature: dict[str, Any], elapsed_seconds: float, duration_seconds: float) -> dict[str, float]:
        if duration_seconds <= 0:
            return {}
        start_offset = float(signature.get("segment_start_seconds", 0.0) or 0.0)
        if elapsed_seconds < start_offset:
            return {}
        active_elapsed = elapsed_seconds - start_offset
        if active_elapsed > duration_seconds:
            return {}
        hold_ratio = clamp(float(signature.get("hold_ratio", 0.16) or 0.16), 0.0, 0.75)
        hold_seconds = duration_seconds * hold_ratio
        eye_lead_ratio = clamp(float(signature.get("eye_lead_ratio", 0.26) or 0.26), 0.0, 0.95)
        head_follow_ratio = max(eye_lead_ratio + 0.06, float(signature.get("head_follow_ratio", 0.56) or 0.56))
        body_follow_ratio = max(head_follow_ratio + 0.08, float(signature.get("body_follow_ratio", 0.88) or 0.88))

        eye_env = _impulse_window(active_elapsed, duration_seconds, lead_ratio=eye_lead_ratio, hold_seconds=hold_seconds)
        head_env = _impulse_window(active_elapsed, duration_seconds, lead_ratio=head_follow_ratio, hold_seconds=hold_seconds * 0.96)
        body_env = _impulse_window(active_elapsed, duration_seconds, lead_ratio=body_follow_ratio, hold_seconds=hold_seconds * 0.9)

        yaw_direction = float(signature.get("yaw_direction", 1.0) or 1.0)
        pitch_direction = float(signature.get("pitch_direction", 1.0) or 1.0)
        roll_direction = float(signature.get("roll_direction", 1.0) or 1.0)
        eye_x = float(signature.get("eye_x_amount", 0.0) or 0.0) * eye_env
        eye_y = float(signature.get("eye_y_amount", 0.0) or 0.0) * eye_env * 0.82
        head_x = (yaw_direction * float(signature.get("yaw_amount", 0.0) or 0.0) + eye_x * 3.8) * head_env
        head_y = (pitch_direction * float(signature.get("pitch_amount", 0.0) or 0.0) + eye_y * 2.0) * body_env * 0.62
        head_z = (roll_direction * float(signature.get("roll_amount", 0.0) or 0.0) + eye_x * 1.8) * body_env
        return {
            "head_x": round(head_x, 4),
            "head_y": round(head_y, 4),
            "head_z": round(head_z, 4),
            "eye_x": round(eye_x, 4),
            "eye_y": round(eye_y, 4),
        }

    def _idle_wander_tracking(self, *, state: dict[str, Any], timestamp: float, envelope: float) -> dict[str, float]:
        if envelope > 0.08:
            return {}
        if isinstance(state.get("idle_stage_signature"), dict):
            return {}
        signature = state.get("idle_wander_signature")
        started_at = float(state.get("idle_wander_started_at_monotonic") or 0.0)
        duration = float(state.get("idle_wander_duration_seconds") or 0.0)
        if not isinstance(signature, dict) or started_at <= 0 or duration <= 0:
            return {}
        elapsed = max(timestamp - started_at, 0.0)
        progress = clamp(elapsed / duration, 0.0, 1.0)
        active = math.sin(progress * math.pi)
        gaze_phase = float(signature.get("gaze_phase", 0.0) or 0.0)
        micro = math.sin((timestamp * 0.44) + gaze_phase)
        micro_y = math.cos((timestamp * 0.33) + gaze_phase * 0.73)
        return {
            "head_x": float(signature.get("anchor_head_x", 0.0)) * active * 1.34,
            "head_y": float(signature.get("anchor_head_y", 0.0)) * active * 1.34,
            "head_z": float(signature.get("anchor_head_z", 0.0)) * active * 1.52,
            "eye_x": float(signature.get("anchor_eye_x", 0.0)) * active * 0.78 + micro * self.profile.idle.eye_x_range * 0.1,
            "eye_y": float(signature.get("anchor_eye_y", 0.0)) * active * 0.76 + micro_y * self.profile.idle.eye_y_range * 0.08,
        }

    def _idle_attention_tracking(self, *, state: dict[str, Any], timestamp: float, envelope: float) -> dict[str, float]:
        if envelope > 0.08:
            return {}
        if isinstance(state.get("idle_stage_signature"), dict):
            return {}
        signature = state.get("idle_attention_signature")
        started_at = float(state.get("idle_attention_started_at_monotonic") or 0.0)
        duration = float(state.get("idle_attention_duration_seconds") or 0.0)
        if not isinstance(signature, dict) or started_at <= 0 or duration <= 0:
            return {}
        elapsed = max(timestamp - started_at, 0.0)
        if elapsed > duration:
            return {}
        hold_ratio = clamp(float(signature.get("hold_ratio", self.profile.idle.attention_hold_ratio) or self.profile.idle.attention_hold_ratio), 0.0, 0.78)
        eye_lead_ratio = clamp(float(signature.get("eye_lead_ratio", self.profile.idle.attention_eye_lead_ratio) or self.profile.idle.attention_eye_lead_ratio), 0.0, 0.9)
        return_softness = clamp(float(signature.get("return_softness", self.profile.idle.attention_return_softness) or self.profile.idle.attention_return_softness), 0.1, 1.2)
        head_follow_gain = float(signature.get("head_follow_gain", self.profile.idle.attention_head_follow_gain) or self.profile.idle.attention_head_follow_gain)
        body_follow_gain = float(signature.get("body_follow_gain", self.profile.idle.attention_body_follow_gain) or self.profile.idle.attention_body_follow_gain)
        hold_seconds = max(duration * hold_ratio, 0.64)
        eye_env = _snap_window(elapsed, duration, lead_ratio=min(0.08, eye_lead_ratio * 0.34), hold_seconds=hold_seconds * 1.06)
        head_env = _impulse_window(elapsed, duration * return_softness, lead_ratio=min(0.4, eye_lead_ratio + 0.1), hold_seconds=hold_seconds * 1.12)
        body_env = _impulse_window(elapsed, duration * max(return_softness, 1.0), lead_ratio=min(0.6, eye_lead_ratio + 0.22), hold_seconds=hold_seconds * 1.02)
        micro_phase = float(signature.get("micro_phase", 0.0) or 0.0)
        look_bias = float(signature.get("look_bias", 0.0) or 0.0)
        micro_x = math.sin((timestamp * 0.42) + micro_phase) * self.profile.idle.attention_eye_x_range * 0.04
        micro_y = math.cos((timestamp * 0.36) + micro_phase * 0.81) * self.profile.idle.attention_eye_y_range * 0.03
        return {
            "head_x": round(float(signature.get("head_x", 0.0) or 0.0) * head_env * head_follow_gain, 4),
            "head_y": round(float(signature.get("head_y", 0.0) or 0.0) * body_env * body_follow_gain + abs(math.sin((elapsed / max(duration, 0.01)) * math.pi)) * 0.24, 4),
            "head_z": round(float(signature.get("head_z", 0.0) or 0.0) * body_env * body_follow_gain, 4),
            "eye_x": round(float(signature.get("target_eye_x", 0.0) or 0.0) * eye_env * 0.82 + micro_x + look_bias * 0.012, 4),
            "eye_y": round(float(signature.get("target_eye_y", 0.0) or 0.0) * eye_env * 0.8 + micro_y, 4),
        }

    def _idle_glance_tracking(self, *, state: dict[str, Any], timestamp: float, envelope: float) -> dict[str, float]:
        if envelope > 0.08:
            return {}
        if isinstance(state.get("idle_stage_signature"), dict):
            return {}
        signature = state.get("idle_glance_signature")
        started_at = float(state.get("idle_glance_started_at_monotonic") or 0.0)
        duration = float(state.get("idle_glance_duration_seconds") or 0.0)
        if not isinstance(signature, dict) or started_at <= 0 or duration <= 0:
            return {}
        elapsed = max(timestamp - started_at, 0.0)
        if elapsed > duration:
            return {}
        hold_ratio = clamp(float(signature.get("hold_ratio", self.profile.idle.glance_hold_ratio) or self.profile.idle.glance_hold_ratio), 0.0, 0.7)
        eye_lead_ratio = clamp(float(signature.get("eye_lead_ratio", 0.32) or 0.32), 0.0, 0.9)
        return_softness = clamp(float(signature.get("return_softness", self.profile.idle.glance_return_softness) or self.profile.idle.glance_return_softness), 0.1, 1.0)
        head_follow_gain = float(signature.get("head_follow_gain", 1.0) or 1.0)
        hold_seconds = max(duration * hold_ratio, 0.24)
        eye_env = _snap_window(elapsed, duration, lead_ratio=min(0.18, eye_lead_ratio * 0.55), hold_seconds=hold_seconds * 0.92)
        head_env = _impulse_window(elapsed, duration * return_softness, lead_ratio=min(0.74, eye_lead_ratio + 0.28), hold_seconds=hold_seconds * 0.72)
        body_env = _impulse_window(elapsed, duration * max(return_softness, 0.85), lead_ratio=min(0.88, eye_lead_ratio + 0.42), hold_seconds=hold_seconds * 0.62)
        return {
            "head_x": round(float(signature.get("glance_head_x", 0.0) or 0.0) * head_env * head_follow_gain, 4),
            "head_y": round(float(signature.get("glance_head_y", 0.0) or 0.0) * body_env * 0.78, 4),
            "head_z": round(float(signature.get("glance_head_z", 0.0) or 0.0) * body_env * max(0.92, head_follow_gain * 0.9), 4),
            "eye_x": round(float(signature.get("glance_eye_x", 0.0) or 0.0) * eye_env, 4),
            "eye_y": round(float(signature.get("glance_eye_y", 0.0) or 0.0) * eye_env, 4),
        }

    def _idle_stage_tracking(self, *, state: dict[str, Any], timestamp: float, envelope: float) -> dict[str, float]:
        if envelope > 0.08:
            return {}
        signature = state.get("idle_stage_signature")
        stage_name = str(state.get("idle_stage_name", ""))
        started_at = float(state.get("idle_stage_started_at_monotonic") or 0.0)
        duration = float(state.get("idle_stage_duration_seconds") or 0.0)
        if not isinstance(signature, dict) or not stage_name or started_at <= 0 or duration <= 0:
            return {}
        elapsed = max(timestamp - started_at, 0.0)
        if elapsed > duration:
            return {}
        spec = self.profile.idle_stages.get(stage_name)
        if spec is None:
            return {}
        entry_ratio = clamp(float(signature.get("entry_ratio", 0.12) or 0.12), 0.04, 0.24)
        release_ratio = clamp(float(signature.get("release_ratio", 0.18) or 0.18), 0.08, 0.32)
        posture_env = _hold_then_release_window(elapsed, duration, entry_ratio=entry_ratio, release_ratio=release_ratio)
        yaw_direction = float(signature.get("yaw_direction", 1.0) or 1.0)
        pitch_direction = float(signature.get("pitch_direction", 1.0) or 1.0)
        roll_direction = float(signature.get("roll_direction", 1.0) or 1.0)
        tracking_gain = float(signature.get("tracking_gain", spec.tracking_gain) or spec.tracking_gain)
        stage_phase = float(signature.get("stage_phase", 0.0) or 0.0)
        micro_phase = float(signature.get("micro_phase", 0.0) or 0.0)
        peek_bias = float(signature.get("peek_bias", 0.0) or 0.0)
        bounce_bias = float(signature.get("bounce_bias", 0.0) or 0.0)
        loop_bias = float(signature.get("loop_bias", 0.0) or 0.0)

        phase = clamp(elapsed / max(duration, 0.001), 0.0, 1.0)
        burst_start_seconds = duration * clamp(float(signature.get("burst_start_ratio", 0.28) or 0.28), 0.08, 0.82)
        burst_duration_seconds = max(0.18, duration * clamp(float(signature.get("burst_duration_ratio", 0.16) or 0.16), 0.08, 0.34))
        correction_start_seconds = duration * clamp(float(signature.get("correction_start_ratio", 0.64) or 0.64), 0.2, 0.92)
        correction_duration_seconds = max(0.16, duration * clamp(float(signature.get("correction_duration_ratio", 0.12) or 0.12), 0.08, 0.3))
        burst_env = _smooth_bump_window(
            elapsed,
            start_seconds=burst_start_seconds,
            duration_seconds=burst_duration_seconds,
        )
        correction_env = _smooth_bump_window(
            elapsed,
            start_seconds=correction_start_seconds,
            duration_seconds=correction_duration_seconds,
        )
        micro = math.sin((timestamp * 0.38) + micro_phase)
        micro_y = math.cos((timestamp * 0.31) + micro_phase * 0.72)
        pulse = math.sin((phase * math.pi) + stage_phase)
        bounce = abs(math.sin((phase * math.pi * 1.2) + stage_phase * 0.42)) - 0.5
        loop = math.cos((phase * math.pi * 0.8) + micro_phase) + loop_bias * 0.16
        burst_soft = math.sin(clamp((elapsed - burst_start_seconds) / max(burst_duration_seconds, 0.001), 0.0, 1.0) * math.pi) if elapsed >= burst_start_seconds else 0.0
        correction_soft = math.sin(clamp((elapsed - correction_start_seconds) / max(correction_duration_seconds, 0.001), 0.0, 1.0) * math.pi) if elapsed >= correction_start_seconds else 0.0

        anchor_head_x = yaw_direction * float(signature.get("anchor_head_x", 0.0) or 0.0)
        anchor_head_y = pitch_direction * float(signature.get("anchor_head_y", 0.0) or 0.0)
        anchor_head_z = roll_direction * float(signature.get("anchor_head_z", 0.0) or 0.0)
        anchor_eye_x = yaw_direction * float(signature.get("anchor_eye_x", 0.0) or 0.0)
        anchor_eye_y = pitch_direction * float(signature.get("anchor_eye_y", 0.0) or 0.0)
        burst_head_x = yaw_direction * float(signature.get("burst_head_x", 0.0) or 0.0)
        burst_head_y = pitch_direction * float(signature.get("burst_head_y", 0.0) or 0.0)
        burst_head_z = roll_direction * float(signature.get("burst_head_z", 0.0) or 0.0)
        burst_eye_x = yaw_direction * float(signature.get("burst_eye_x", 0.0) or 0.0)
        burst_eye_y = pitch_direction * float(signature.get("burst_eye_y", 0.0) or 0.0)

        pattern = str(signature.get("pattern", spec.pattern) or spec.pattern)
        if pattern == "peek":
            head_x = anchor_head_x * posture_env * 1.16 + burst_head_x * burst_env * 0.34 + burst_head_x * burst_soft * 0.18 - burst_head_x * correction_env * 0.05
            head_y = anchor_head_y * posture_env * 0.92 + burst_head_y * burst_env * 0.2 + abs(pulse) * 0.14
            head_z = anchor_head_z * posture_env * 1.14 + burst_head_z * burst_env * 0.26 + burst_head_z * burst_soft * 0.12 - burst_head_z * correction_env * 0.04
            eye_x = anchor_eye_x * posture_env * 0.8 + burst_eye_x * burst_env * 0.44 + peek_bias * 0.04 - burst_eye_x * correction_env * 0.03
            eye_y = anchor_eye_y * posture_env * 0.82 + burst_eye_y * burst_env * 0.34 - burst_eye_y * correction_env * 0.02
        elif pattern == "bounce":
            head_x = anchor_head_x * posture_env * 0.96 + burst_head_x * burst_env * 0.22 + loop * 0.12
            head_y = anchor_head_y * posture_env * 0.82 + burst_head_y * burst_env * (0.58 + bounce_bias * 0.12) + burst_head_y * burst_soft * 0.12 - burst_head_y * correction_env * 0.08 + bounce * 0.18
            head_z = anchor_head_z * posture_env * 1.06 + burst_head_z * burst_env * 0.22 - burst_head_z * correction_env * 0.05
            eye_x = anchor_eye_x * posture_env * 0.7 + burst_eye_x * burst_env * 0.24 + pulse * 0.04
            eye_y = anchor_eye_y * posture_env * 0.74 + burst_eye_y * burst_env * 0.24 + bounce * 0.03
        else:
            head_x = anchor_head_x * posture_env * 1.12 + burst_head_x * burst_env * 0.18 + burst_head_x * burst_soft * 0.1 - burst_head_x * correction_env * 0.04 + loop * 0.1
            head_y = anchor_head_y * posture_env * 0.94 + abs(pulse) * 0.12 - burst_head_y * correction_env * 0.02
            head_z = anchor_head_z * posture_env * 1.08 + burst_head_z * burst_env * 0.14 - burst_head_z * correction_env * 0.03
            eye_x = anchor_eye_x * posture_env * 0.78 + burst_eye_x * burst_env * 0.22 + micro * 0.03
            eye_y = anchor_eye_y * posture_env * 0.82 + burst_eye_y * burst_env * 0.18 + micro_y * 0.02

        stage_expression = str(signature.get("expression", spec.expression) or spec.expression)
        if stage_expression:
            expression_bias = self._expression_bias(stage_expression)
            head_x += float(expression_bias.get("head_x", 0.0))
            head_y += float(expression_bias.get("head_y", 0.0))
            head_z += float(expression_bias.get("head_z", 0.0))
            eye_x += float(expression_bias.get("eye_x", 0.0))
            eye_y += float(expression_bias.get("eye_y", 0.0))

        smile = max(
            0.0,
            float(signature.get("smile_boost", spec.smile_boost) or spec.smile_boost)
            * max(posture_env * 0.82, burst_env, correction_env * 0.34),
        )
        if stage_expression:
            smile += float(self._expression_bias(stage_expression).get("smile", 0.0))
        return {
            "head_x": round(head_x * tracking_gain, 4),
            "head_y": round(head_y * tracking_gain, 4),
            "head_z": round(head_z * tracking_gain, 4),
            "eye_x": round(eye_x * tracking_gain, 4),
            "eye_y": round(eye_y * tracking_gain, 4),
            "smile": round(smile, 4),
        }


def _merge_tracking(base: dict[str, float], extra: dict[str, float]) -> dict[str, float]:
    merged = dict(base)
    for key, value in extra.items():
        merged[key] = round(float(merged.get(key, 0.0)) + float(value), 4)
    return merged


def _segment_ratio(event: SpeechEvent) -> float:
    text = event.text.strip()
    if not text:
        return 0.0
    revealed_count = int((event.metadata or {}).get("revealed_count", 0) or 0)
    if revealed_count <= 0:
        return 0.0
    return clamp(revealed_count / max(1, len(text)), 0.0, 1.0)


def _stable_units(seed_text: str, *, count: int) -> list[float]:
    units: list[float] = []
    salt = 0
    while len(units) < count:
        digest = hashlib.sha256(f"{seed_text}|{salt}".encode("utf-8")).digest()
        salt += 1
        for index in range(0, len(digest), 4):
            chunk = digest[index : index + 4]
            if len(chunk) < 4:
                continue
            value = int.from_bytes(chunk, byteorder="big", signed=False)
            units.append(value / 0xFFFFFFFF)
            if len(units) >= count:
                break
    return units


def _range(lower: float, upper: float, unit: float) -> float:
    return lower + (upper - lower) * clamp(unit, 0.0, 1.0)


def _direction(unit: float) -> float:
    return -1.0 if unit < 0.5 else 1.0


def _signed_span(span: float, *, direction_unit: float, magnitude_unit: float, min_ratio: float) -> float:
    direction = _direction(direction_unit)
    magnitude = _range(span * min_ratio, span, magnitude_unit)
    return direction * magnitude


def _impulse_window(elapsed_seconds: float, duration_seconds: float, *, lead_ratio: float, hold_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    lead_ratio = clamp(lead_ratio, 0.0, 0.98)
    rise_seconds = max(duration_seconds * lead_ratio, 0.05)
    if elapsed_seconds <= rise_seconds:
        return clamp(elapsed_seconds / rise_seconds, 0.0, 1.0)
    if elapsed_seconds <= rise_seconds + hold_seconds:
        return 1.0
    decay_seconds = max(duration_seconds - rise_seconds - hold_seconds, 0.05)
    decay_elapsed = elapsed_seconds - rise_seconds - hold_seconds
    return clamp(1.0 - (decay_elapsed / decay_seconds), 0.0, 1.0)


def _snap_window(elapsed_seconds: float, duration_seconds: float, *, lead_ratio: float, hold_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    lead_ratio = clamp(lead_ratio, 0.0, 0.45)
    rise_seconds = max(duration_seconds * lead_ratio, 0.025)
    if elapsed_seconds <= rise_seconds:
        return clamp(elapsed_seconds / rise_seconds, 0.0, 1.0)
    if elapsed_seconds <= rise_seconds + hold_seconds:
        return 1.0
    decay_seconds = max(duration_seconds - rise_seconds - hold_seconds, 0.04)
    decay_elapsed = elapsed_seconds - rise_seconds - hold_seconds
    return clamp(1.0 - (decay_elapsed / decay_seconds), 0.0, 1.0)


def _hold_then_release_window(elapsed_seconds: float, duration_seconds: float, *, entry_ratio: float, release_ratio: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    entry_seconds = max(duration_seconds * clamp(entry_ratio, 0.02, 0.32), 0.05)
    release_seconds = max(duration_seconds * clamp(release_ratio, 0.04, 0.36), 0.08)
    release_start = max(duration_seconds - release_seconds, entry_seconds)
    if elapsed_seconds <= entry_seconds:
        return clamp(elapsed_seconds / entry_seconds, 0.0, 1.0)
    if elapsed_seconds <= release_start:
        return 1.0
    decay_elapsed = elapsed_seconds - release_start
    return clamp(1.0 - (decay_elapsed / max(release_seconds, 0.05)), 0.0, 1.0)


def _smooth_bump_window(elapsed_seconds: float, *, start_seconds: float, duration_seconds: float) -> float:
    if duration_seconds <= 0 or elapsed_seconds < start_seconds:
        return 0.0
    phase = clamp((elapsed_seconds - start_seconds) / duration_seconds, 0.0, 1.0)
    return math.sin(phase * math.pi) ** 2


def _blend_idle_heading(previous: float, target: float, *, max_delta: float) -> float:
    delta = clamp(target - previous, -max_delta, max_delta)
    blended = previous + delta * 0.72
    if previous != 0.0 and target != 0.0 and ((previous > 0) != (target > 0)) and abs(delta) > max_delta * 0.48:
        blended = previous + delta * 0.42
    return blended
