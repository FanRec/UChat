from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


CommandType = Literal["speech_plan", "expression", "motion", "turn_end", "clear", "set_baseline"]
SpeechAction = Literal["segment_start", "segment_progress", "segment_complete", "turn_end", "clear"]


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass(slots=True)
class BodyCommand:
    command_id: str
    trace_id: str
    body_id: str
    command_type: CommandType = "speech_plan"
    generation_id: int = 1
    segment_index: int | None = None
    expression: str | None = None
    motion: str | None = None
    intensity: float = 0.5
    duration_ms: int = 0
    sync_to_audio: bool = False
    commit_mode: str = "transient"
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, body: dict[str, Any]) -> "BodyCommand":
        metadata = dict(body.get("metadata") or {})
        return cls(
            command_id=str(body.get("command_id", "")),
            trace_id=str(body.get("trace_id", "")),
            body_id=str(body.get("body_id", "")),
            command_type=str(body.get("command_type", "speech_plan") or "speech_plan"),  # type: ignore[arg-type]
            generation_id=max(1, int(body.get("generation_id", 1) or 1)),
            segment_index=_optional_int(body.get("segment_index")),
            expression=_optional_text(body.get("expression")),
            motion=_optional_text(body.get("motion")),
            intensity=clamp(float(body.get("intensity", 0.5) or 0.5), 0.0, 1.0),
            duration_ms=max(0, int(body.get("duration_ms", 0) or 0)),
            sync_to_audio=bool(body.get("sync_to_audio", False)),
            commit_mode=str(metadata.pop("commit_mode", body.get("commit_mode", "transient")) or "transient"),
            text=str(body.get("text", "")),
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SpeechEvent:
    trace_id: str
    task_id: str
    action: SpeechAction = "segment_start"
    generation_id: int = 1
    segment_index: int | None = None
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, body: dict[str, Any]) -> "SpeechEvent":
        return cls(
            trace_id=str(body.get("trace_id", "")),
            task_id=str(body.get("task_id", "")),
            action=str(body.get("action", "segment_start") or "segment_start"),  # type: ignore[arg-type]
            generation_id=max(1, int(body.get("generation_id", 1) or 1)),
            segment_index=_optional_int(body.get("segment_index")),
            text=str(body.get("text", "")),
            metadata=dict(body.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExpressionSpec:
    hotkey: str = ""
    tracking_bias: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MotionSpec:
    hotkey: str = ""
    cooldown_ms: int = 0
    duration_ms: int = 0
    tracking_boost: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class IdleProfile:
    tick_hz: float = 6.0
    base_intensity: float = 0.22
    speaking_boost: float = 1.55
    head_x_range: float = 2.6
    head_y_range: float = 1.7
    head_z_range: float = 1.2
    eye_x_range: float = 0.16
    eye_y_range: float = 0.1
    smile_floor: float = 0.06
    smile_range: float = 0.14
    breath_floor: float = 0.10
    breath_range: float = 0.12
    breath_head_y_range: float = 0.42
    breath_head_z_range: float = 0.18
    tracking_alpha_idle: float = 0.22
    tracking_alpha_speaking: float = 0.3
    tracking_alpha_event: float = 0.58
    tracking_alpha_head_idle: float = 0.22
    tracking_alpha_head_speaking: float = 0.28
    tracking_alpha_head_event: float = 0.54
    tracking_alpha_body_idle: float = 0.16
    tracking_alpha_body_speaking: float = 0.2
    tracking_alpha_body_event: float = 0.4
    tracking_alpha_eye_idle: float = 0.34
    tracking_alpha_eye_speaking: float = 0.46
    tracking_alpha_eye_event: float = 0.7
    tracking_alpha_smile_idle: float = 0.2
    tracking_alpha_smile_speaking: float = 0.24
    tracking_alpha_smile_event: float = 0.4
    wander_head_x_range: float = 1.8
    wander_head_y_range: float = 0.95
    wander_head_z_range: float = 0.72
    wander_eye_x_range: float = 0.32
    wander_eye_y_range: float = 0.22
    wander_duration_min_s: float = 2.4
    wander_duration_max_s: float = 4.8
    glance_probability: float = 0.28
    glance_head_x_range: float = 2.5
    glance_head_y_range: float = 0.85
    glance_head_z_range: float = 1.1
    glance_eye_x_range: float = 0.54
    glance_eye_y_range: float = 0.3
    glance_duration_min_s: float = 0.75
    glance_duration_max_s: float = 1.35
    glance_hold_ratio: float = 0.24
    glance_return_softness: float = 0.72
    attention_head_x_range: float = 4.8
    attention_head_y_range: float = 2.2
    attention_head_z_range: float = 2.0
    attention_eye_x_range: float = 0.58
    attention_eye_y_range: float = 0.34
    attention_duration_min_s: float = 1.8
    attention_duration_max_s: float = 4.4
    attention_hold_ratio: float = 0.36
    attention_return_softness: float = 0.9
    attention_eye_lead_ratio: float = 0.16
    attention_head_follow_gain: float = 1.5
    attention_body_follow_gain: float = 1.12
    performance_head_x_range: float = 4.6
    performance_head_y_range: float = 3.8
    performance_head_z_range: float = 3.1
    performance_eye_x_range: float = 0.24
    performance_eye_y_range: float = 0.16
    performance_bounce_gain: float = 1.0
    performance_loop_gain: float = 1.0


@dataclass(slots=True)
class IdleStageRuntimeProfile:
    enabled: bool = False
    cooldown_min_s: float = 1.4
    cooldown_max_s: float = 2.8
    suppression_after_speaking_s: float = 1.4
    allow_hotkey: bool = True


@dataclass(slots=True)
class IdleStageSpec:
    pattern: str = "sway"
    expression: str = ""
    motion: str = ""
    weight: float = 1.0
    duration_min_s: float = 1.6
    duration_max_s: float = 3.2
    hold_ratio: float = 0.24
    head_x_range: float = 4.0
    head_y_range: float = 2.0
    head_z_range: float = 2.0
    eye_x_range: float = 0.32
    eye_y_range: float = 0.18
    smile_boost: float = 0.04
    bounce_gain: float = 1.0
    loop_gain: float = 1.0
    eye_lead_ratio: float = 0.16
    head_follow_ratio: float = 0.38
    body_follow_ratio: float = 0.58
    return_softness: float = 0.9
    switch_chance: float = 0.42
    tracking_gain: float = 1.0
    motion_intensity: float = 0.55
    hotkey_probability: float = 0.0


@dataclass(slots=True)
class SpeechReactiveProfile:
    enabled: bool = True
    speaking_expression: str = "soft_smile"
    motion_cycle: list[str] = field(default_factory=list)
    motion_cooldown_ms: int = 1600
    segment_complete_hold_ms: int = 420
    segment_bridge_hold_ms: int = 900
    speaking_yaw_range: float = 2.2
    speaking_pitch_range: float = 1.45
    speaking_roll_range: float = 1.1
    speaking_anchor_yaw_range: float = 1.2
    speaking_anchor_pitch_range: float = 0.55
    speaking_anchor_roll_range: float = 0.7
    sway_yaw_range: float = 1.15
    sway_roll_range: float = 0.75
    sway_pitch_range: float = 0.55
    sway_rate_min_hz: float = 0.7
    sway_rate_max_hz: float = 1.35
    signature_scale_min: float = 0.82
    signature_scale_max: float = 1.28
    onset_emphasis_min: float = 0.55
    onset_emphasis_max: float = 1.0
    settle_strength_min: float = 0.38
    settle_strength_max: float = 0.82
    smile_boost_min: float = 0.02
    smile_boost_max: float = 0.07
    cooldown_falloff_ms: int = 760
    speaking_eye_x_range: float = 0.28
    speaking_eye_y_range: float = 0.18
    speaking_eye_anchor_x_range: float = 0.12
    speaking_eye_anchor_y_range: float = 0.08
    glance_rate_min_hz: float = 0.55
    glance_rate_max_hz: float = 1.15
    segment_duration_floor_ms: int = 680
    segment_duration_per_char_ms: float = 64.0
    accent_window_ratio_min: float = 0.32
    accent_window_ratio_max: float = 0.74
    accent_duration_ratio_min: float = 0.12
    accent_duration_ratio_max: float = 0.26
    accent_hold_ratio_min: float = 0.08
    accent_hold_ratio_max: float = 0.2
    accent_yaw_range: float = 3.3
    accent_pitch_range: float = 1.45
    accent_roll_range: float = 1.55
    accent_eye_x_range: float = 0.5
    accent_eye_y_range: float = 0.24
    accent_switch_chance: float = 0.42
    accent_eye_lead_ratio: float = 0.4
    accent_head_follow_ratio: float = 0.72
    accent_body_follow_ratio: float = 1.08
    figure8_mix_min: float = 0.08
    figure8_mix_max: float = 0.26
    retarget_window_ratio_min: float = 0.42
    retarget_window_ratio_max: float = 0.82
    retarget_duration_ratio_min: float = 0.16
    retarget_duration_ratio_max: float = 0.3
    retarget_hold_ratio_min: float = 0.1
    retarget_hold_ratio_max: float = 0.24
    retarget_switch_chance: float = 0.58
    retarget_eye_lead_ratio: float = 0.26
    retarget_head_follow_ratio: float = 0.56
    retarget_body_follow_ratio: float = 0.88
    retarget_yaw_range: float = 2.8
    retarget_pitch_range: float = 1.05
    retarget_roll_range: float = 1.2
    retarget_eye_x_range: float = 0.48
    retarget_eye_y_range: float = 0.22


@dataclass(slots=True)
class BodyProfile:
    name: str
    path: Path
    body_id: str
    backend: str
    model_hint: str = ""
    tracking_inputs: dict[str, str] = field(default_factory=dict)
    expressions: dict[str, ExpressionSpec] = field(default_factory=dict)
    motions: dict[str, MotionSpec] = field(default_factory=dict)
    idle: IdleProfile = field(default_factory=IdleProfile)
    idle_stage_runtime: IdleStageRuntimeProfile = field(default_factory=IdleStageRuntimeProfile)
    idle_stages: dict[str, IdleStageSpec] = field(default_factory=dict)
    speech_reactive: SpeechReactiveProfile = field(default_factory=SpeechReactiveProfile)
    baseline_profiles: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "body_id": self.body_id,
            "backend": self.backend,
            "model_hint": self.model_hint,
            "tracking_inputs": dict(self.tracking_inputs),
            "expressions": {key: {"hotkey": value.hotkey, "tracking_bias": dict(value.tracking_bias)} for key, value in self.expressions.items()},
            "motions": {
                key: {
                    "hotkey": value.hotkey,
                    "cooldown_ms": value.cooldown_ms,
                    "duration_ms": value.duration_ms,
                    "tracking_boost": dict(value.tracking_boost),
                }
                for key, value in self.motions.items()
            },
            "idle": asdict(self.idle),
            "idle_stage_runtime": asdict(self.idle_stage_runtime),
            "idle_stages": {key: asdict(value) for key, value in self.idle_stages.items()},
            "speech_reactive": asdict(self.speech_reactive),
            "baseline_profiles": dict(self.baseline_profiles),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class DispatchPlan:
    operation: str
    expression: str | None = None
    motion: str | None = None
    hotkeys: list[str] = field(default_factory=list)
    logical_tracking: dict[str, float] = field(default_factory=dict)
    speaking: bool | None = None
    speech_phase: str | None = None
    reactive_level: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IdleFrame:
    logical_tracking: dict[str, float]
    summary: dict[str, float]
    speech_phase: str
    reactive_level: float
    speaking_signature: dict[str, Any] | None = None
    envelope_summary: dict[str, Any] = field(default_factory=dict)
    stage_summary: dict[str, Any] = field(default_factory=dict)
    clear_speaking_signature: bool = False


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
