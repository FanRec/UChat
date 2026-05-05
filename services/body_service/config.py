from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.body_service.models import BodyProfile, ExpressionSpec, IdleProfile, IdleStageRuntimeProfile, IdleStageSpec, MotionSpec, SpeechReactiveProfile


class BodyServiceConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceBindConfig:
    service_name: str
    base_url: str
    listen_host: str
    listen_port: int


@dataclass(frozen=True)
class BodyRuntimeConfig:
    body_id: str
    active_profile: str
    idle_enabled: bool
    event_history_limit: int


@dataclass(frozen=True)
class PathConfig:
    profile_dir: Path
    debug_dir: Path
    auth_token_path: Path


@dataclass(frozen=True)
class DebugConfig:
    enabled: bool
    print_panels: bool
    write_latest_state: bool


@dataclass(frozen=True)
class VTSConfig:
    ws_url: str
    plugin_name: str
    plugin_developer: str
    connect_timeout_ms: int
    request_timeout_ms: int


@dataclass(frozen=True)
class BackendConfig:
    type: str
    connect_on_startup: bool
    vts: VTSConfig


@dataclass(frozen=True)
class BodyServiceConfig:
    service: ServiceBindConfig
    body: BodyRuntimeConfig
    paths: PathConfig
    debug: DebugConfig
    backend: BackendConfig

    @classmethod
    def load(cls, path: str | Path | None = None) -> "BodyServiceConfig":
        config_path = Path(path or default_config_path())
        if not config_path.exists():
            raise BodyServiceConfigError(f"config file not found: {config_path}")
        repo_root = config_path.resolve().parents[3]
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)

        service_name = str(raw.get("service_name", "body_service")).strip() or "body_service"
        base_url = str(raw.get("base_url", "http://127.0.0.1:8103")).strip().rstrip("/")
        host = str(raw.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        port = int(raw.get("port", 8103))

        body_raw = _dict(raw.get("body"))
        paths_raw = _dict(raw.get("paths"))
        debug_raw = _dict(raw.get("debug"))
        backend_raw = _dict(raw.get("backend"))
        vts_raw = _dict(backend_raw.get("vts"))

        return cls(
            service=ServiceBindConfig(
                service_name=service_name,
                base_url=base_url,
                listen_host=host,
                listen_port=port,
            ),
            body=BodyRuntimeConfig(
                body_id=str(body_raw.get("body_id", "hiyori_vts")).strip() or "hiyori_vts",
                active_profile=str(body_raw.get("active_profile", "hiyori_vts")).strip() or "hiyori_vts",
                idle_enabled=bool(body_raw.get("idle_enabled", True)),
                event_history_limit=max(10, int(body_raw.get("event_history_limit", 80) or 80)),
            ),
            paths=PathConfig(
                profile_dir=_resolve_path(repo_root, Path(str(paths_raw.get("profile_dir", "services/body_service/config/body_profiles")))),
                debug_dir=_resolve_path(repo_root, Path(str(paths_raw.get("debug_dir", "debug/body_service")))),
                auth_token_path=_resolve_path(repo_root, Path(str(paths_raw.get("auth_token_path", "data/body_service/vts_auth_token.txt")))),
            ),
            debug=DebugConfig(
                enabled=bool(debug_raw.get("enabled", True)),
                print_panels=bool(debug_raw.get("print_panels", True)),
                write_latest_state=bool(debug_raw.get("write_latest_state", True)),
            ),
            backend=BackendConfig(
                type=str(backend_raw.get("type", "vts")).strip() or "vts",
                connect_on_startup=bool(backend_raw.get("connect_on_startup", True)),
                vts=VTSConfig(
                    ws_url=str(vts_raw.get("ws_url", "ws://127.0.0.1:8001")).strip() or "ws://127.0.0.1:8001",
                    plugin_name=str(vts_raw.get("plugin_name", "UChat Body Service")).strip() or "UChat Body Service",
                    plugin_developer=str(vts_raw.get("plugin_developer", "FanRec")).strip() or "FanRec",
                    connect_timeout_ms=max(100, int(vts_raw.get("connect_timeout_ms", 1200) or 1200)),
                    request_timeout_ms=max(100, int(vts_raw.get("request_timeout_ms", 1200) or 1200)),
                ),
            ),
        )

    def profile_path(self, name: str | None = None) -> Path:
        profile_name = name or self.body.active_profile
        return self.paths.profile_dir / f"{profile_name}.toml"


def load_body_profile(path: str | Path) -> BodyProfile:
    profile_path = Path(path)
    if not profile_path.exists():
        raise BodyServiceConfigError(f"body profile not found: {profile_path}")
    with profile_path.open("rb") as fh:
        raw = tomllib.load(fh)

    name = profile_path.stem
    tracking_raw = _dict(raw.get("tracking"))
    baseline_raw = _dict(raw.get("baseline_profiles"))
    idle_raw = _dict(raw.get("idle"))
    idle_stage_runtime_raw = _dict(raw.get("idle_stage_runtime"))
    idle_stages_raw = _dict(raw.get("idle_stages"))
    speech_raw = _dict(raw.get("speech_reactive"))
    expressions_raw = _dict(raw.get("expressions"))
    motions_raw = _dict(raw.get("motions"))

    expressions: dict[str, ExpressionSpec] = {}
    for key, value in expressions_raw.items():
        value_map = _dict(value)
        expressions[key] = ExpressionSpec(
            hotkey=str(value_map.get("hotkey", "")).strip(),
            tracking_bias=_float_map(value_map.get("tracking_bias")),
        )

    motions: dict[str, MotionSpec] = {}
    for key, value in motions_raw.items():
        value_map = _dict(value)
        motions[key] = MotionSpec(
            hotkey=str(value_map.get("hotkey", "")).strip(),
            cooldown_ms=max(0, int(value_map.get("cooldown_ms", 0) or 0)),
            duration_ms=max(0, int(value_map.get("duration_ms", 0) or 0)),
            tracking_boost=_float_map(value_map.get("tracking_boost")),
        )

    idle_stages: dict[str, IdleStageSpec] = {}
    for key, value in idle_stages_raw.items():
        value_map = _dict(value)
        idle_stages[key] = IdleStageSpec(
            pattern=str(value_map.get("pattern", "sway")).strip() or "sway",
            expression=str(value_map.get("expression", "")).strip(),
            motion=str(value_map.get("motion", "")).strip(),
            weight=max(0.0, float(value_map.get("weight", 1.0) or 1.0)),
            duration_min_s=max(0.2, float(value_map.get("duration_min_s", 1.6) or 1.6)),
            duration_max_s=max(0.2, float(value_map.get("duration_max_s", 3.2) or 3.2)),
            hold_ratio=float(value_map.get("hold_ratio", 0.24) or 0.24),
            head_x_range=float(value_map.get("head_x_range", 4.0) or 4.0),
            head_y_range=float(value_map.get("head_y_range", 2.0) or 2.0),
            head_z_range=float(value_map.get("head_z_range", 2.0) or 2.0),
            eye_x_range=float(value_map.get("eye_x_range", 0.32) or 0.32),
            eye_y_range=float(value_map.get("eye_y_range", 0.18) or 0.18),
            smile_boost=float(value_map.get("smile_boost", 0.04) or 0.04),
            bounce_gain=float(value_map.get("bounce_gain", 1.0) or 1.0),
            loop_gain=float(value_map.get("loop_gain", 1.0) or 1.0),
            eye_lead_ratio=float(value_map.get("eye_lead_ratio", 0.16) or 0.16),
            head_follow_ratio=float(value_map.get("head_follow_ratio", 0.38) or 0.38),
            body_follow_ratio=float(value_map.get("body_follow_ratio", 0.58) or 0.58),
            return_softness=float(value_map.get("return_softness", 0.9) or 0.9),
            switch_chance=float(value_map.get("switch_chance", 0.42) or 0.42),
            tracking_gain=float(value_map.get("tracking_gain", 1.0) or 1.0),
            motion_intensity=float(value_map.get("motion_intensity", 0.55) or 0.55),
            hotkey_probability=float(value_map.get("hotkey_probability", 0.0) or 0.0),
        )

    return BodyProfile(
        name=name,
        path=profile_path.resolve(),
        body_id=str(raw.get("body_id", "hiyori_vts")).strip() or "hiyori_vts",
        backend=str(raw.get("backend", "vts")).strip() or "vts",
        model_hint=str(raw.get("model_hint", "")).strip(),
        tracking_inputs={str(key): str(value).strip() for key, value in tracking_raw.items() if str(value).strip()},
        expressions=expressions,
        motions=motions,
        idle=IdleProfile(
            tick_hz=float(idle_raw.get("tick_hz", 6.0) or 6.0),
            base_intensity=float(idle_raw.get("base_intensity", 0.22) or 0.22),
            speaking_boost=float(idle_raw.get("speaking_boost", 1.55) or 1.55),
            head_x_range=float(idle_raw.get("head_x_range", 2.6) or 2.6),
            head_y_range=float(idle_raw.get("head_y_range", 1.7) or 1.7),
            head_z_range=float(idle_raw.get("head_z_range", 1.2) or 1.2),
            eye_x_range=float(idle_raw.get("eye_x_range", 0.16) or 0.16),
            eye_y_range=float(idle_raw.get("eye_y_range", 0.10) or 0.10),
            smile_floor=float(idle_raw.get("smile_floor", 0.06) or 0.06),
            smile_range=float(idle_raw.get("smile_range", 0.14) or 0.14),
            breath_floor=float(idle_raw.get("breath_floor", 0.10) or 0.10),
            breath_range=float(idle_raw.get("breath_range", 0.12) or 0.12),
            breath_head_y_range=float(idle_raw.get("breath_head_y_range", 0.42) or 0.42),
            breath_head_z_range=float(idle_raw.get("breath_head_z_range", 0.18) or 0.18),
            tracking_alpha_idle=float(idle_raw.get("tracking_alpha_idle", 0.22) or 0.22),
            tracking_alpha_speaking=float(idle_raw.get("tracking_alpha_speaking", 0.30) or 0.30),
            tracking_alpha_event=float(idle_raw.get("tracking_alpha_event", 0.58) or 0.58),
            tracking_alpha_head_idle=float(idle_raw.get("tracking_alpha_head_idle", idle_raw.get("tracking_alpha_idle", 0.22)) or 0.22),
            tracking_alpha_head_speaking=float(idle_raw.get("tracking_alpha_head_speaking", idle_raw.get("tracking_alpha_speaking", 0.30)) or 0.30),
            tracking_alpha_head_event=float(idle_raw.get("tracking_alpha_head_event", idle_raw.get("tracking_alpha_event", 0.58)) or 0.58),
            tracking_alpha_body_idle=float(idle_raw.get("tracking_alpha_body_idle", 0.16) or 0.16),
            tracking_alpha_body_speaking=float(idle_raw.get("tracking_alpha_body_speaking", 0.20) or 0.20),
            tracking_alpha_body_event=float(idle_raw.get("tracking_alpha_body_event", 0.40) or 0.40),
            tracking_alpha_eye_idle=float(idle_raw.get("tracking_alpha_eye_idle", 0.34) or 0.34),
            tracking_alpha_eye_speaking=float(idle_raw.get("tracking_alpha_eye_speaking", 0.46) or 0.46),
            tracking_alpha_eye_event=float(idle_raw.get("tracking_alpha_eye_event", 0.70) or 0.70),
            tracking_alpha_smile_idle=float(idle_raw.get("tracking_alpha_smile_idle", 0.20) or 0.20),
            tracking_alpha_smile_speaking=float(idle_raw.get("tracking_alpha_smile_speaking", 0.24) or 0.24),
            tracking_alpha_smile_event=float(idle_raw.get("tracking_alpha_smile_event", 0.40) or 0.40),
            wander_head_x_range=float(idle_raw.get("wander_head_x_range", 1.8) or 1.8),
            wander_head_y_range=float(idle_raw.get("wander_head_y_range", 0.95) or 0.95),
            wander_head_z_range=float(idle_raw.get("wander_head_z_range", 0.72) or 0.72),
            wander_eye_x_range=float(idle_raw.get("wander_eye_x_range", 0.32) or 0.32),
            wander_eye_y_range=float(idle_raw.get("wander_eye_y_range", 0.22) or 0.22),
            wander_duration_min_s=float(idle_raw.get("wander_duration_min_s", 2.4) or 2.4),
            wander_duration_max_s=float(idle_raw.get("wander_duration_max_s", 4.8) or 4.8),
            glance_probability=float(idle_raw.get("glance_probability", 0.28) or 0.28),
            glance_head_x_range=float(idle_raw.get("glance_head_x_range", 2.5) or 2.5),
            glance_head_y_range=float(idle_raw.get("glance_head_y_range", 0.85) or 0.85),
            glance_head_z_range=float(idle_raw.get("glance_head_z_range", 1.1) or 1.1),
            glance_eye_x_range=float(idle_raw.get("glance_eye_x_range", 0.54) or 0.54),
            glance_eye_y_range=float(idle_raw.get("glance_eye_y_range", 0.30) or 0.30),
            glance_duration_min_s=float(idle_raw.get("glance_duration_min_s", 0.75) or 0.75),
            glance_duration_max_s=float(idle_raw.get("glance_duration_max_s", 1.35) or 1.35),
            glance_hold_ratio=float(idle_raw.get("glance_hold_ratio", 0.24) or 0.24),
            glance_return_softness=float(idle_raw.get("glance_return_softness", 0.72) or 0.72),
            attention_head_x_range=float(idle_raw.get("attention_head_x_range", 4.8) or 4.8),
            attention_head_y_range=float(idle_raw.get("attention_head_y_range", 2.2) or 2.2),
            attention_head_z_range=float(idle_raw.get("attention_head_z_range", 2.0) or 2.0),
            attention_eye_x_range=float(idle_raw.get("attention_eye_x_range", 0.58) or 0.58),
            attention_eye_y_range=float(idle_raw.get("attention_eye_y_range", 0.34) or 0.34),
            attention_duration_min_s=float(idle_raw.get("attention_duration_min_s", 1.8) or 1.8),
            attention_duration_max_s=float(idle_raw.get("attention_duration_max_s", 4.4) or 4.4),
            attention_hold_ratio=float(idle_raw.get("attention_hold_ratio", 0.36) or 0.36),
            attention_return_softness=float(idle_raw.get("attention_return_softness", 0.90) or 0.90),
            attention_eye_lead_ratio=float(idle_raw.get("attention_eye_lead_ratio", 0.16) or 0.16),
            attention_head_follow_gain=float(idle_raw.get("attention_head_follow_gain", 1.5) or 1.5),
            attention_body_follow_gain=float(idle_raw.get("attention_body_follow_gain", 1.12) or 1.12),
            performance_head_x_range=float(idle_raw.get("performance_head_x_range", 4.6) or 4.6),
            performance_head_y_range=float(idle_raw.get("performance_head_y_range", 3.8) or 3.8),
            performance_head_z_range=float(idle_raw.get("performance_head_z_range", 3.1) or 3.1),
            performance_eye_x_range=float(idle_raw.get("performance_eye_x_range", 0.24) or 0.24),
            performance_eye_y_range=float(idle_raw.get("performance_eye_y_range", 0.16) or 0.16),
            performance_bounce_gain=float(idle_raw.get("performance_bounce_gain", 1.0) or 1.0),
            performance_loop_gain=float(idle_raw.get("performance_loop_gain", 1.0) or 1.0),
        ),
        idle_stage_runtime=IdleStageRuntimeProfile(
            enabled=bool(idle_stage_runtime_raw.get("enabled", bool(idle_stages))),
            cooldown_min_s=max(0.2, float(idle_stage_runtime_raw.get("cooldown_min_s", 1.4) or 1.4)),
            cooldown_max_s=max(0.2, float(idle_stage_runtime_raw.get("cooldown_max_s", 2.8) or 2.8)),
            suppression_after_speaking_s=max(0.0, float(idle_stage_runtime_raw.get("suppression_after_speaking_s", 1.4) or 1.4)),
            allow_hotkey=bool(idle_stage_runtime_raw.get("allow_hotkey", True)),
        ),
        idle_stages=idle_stages,
        speech_reactive=SpeechReactiveProfile(
            enabled=bool(speech_raw.get("enabled", True)),
            speaking_expression=str(speech_raw.get("speaking_expression", "soft_smile")).strip() or "soft_smile",
            motion_cycle=[str(item).strip() for item in list(speech_raw.get("motion_cycle", [])) if str(item).strip()],
            motion_cooldown_ms=max(0, int(speech_raw.get("motion_cooldown_ms", 1600) or 1600)),
            segment_complete_hold_ms=max(0, int(speech_raw.get("segment_complete_hold_ms", 420) or 420)),
            segment_bridge_hold_ms=max(0, int(speech_raw.get("segment_bridge_hold_ms", 900) or 900)),
            speaking_yaw_range=float(speech_raw.get("speaking_yaw_range", 2.2) or 2.2),
            speaking_pitch_range=float(speech_raw.get("speaking_pitch_range", 1.45) or 1.45),
            speaking_roll_range=float(speech_raw.get("speaking_roll_range", 1.1) or 1.1),
            speaking_anchor_yaw_range=float(speech_raw.get("speaking_anchor_yaw_range", 1.2) or 1.2),
            speaking_anchor_pitch_range=float(speech_raw.get("speaking_anchor_pitch_range", 0.55) or 0.55),
            speaking_anchor_roll_range=float(speech_raw.get("speaking_anchor_roll_range", 0.7) or 0.7),
            sway_yaw_range=float(speech_raw.get("sway_yaw_range", 1.15) or 1.15),
            sway_roll_range=float(speech_raw.get("sway_roll_range", 0.75) or 0.75),
            sway_pitch_range=float(speech_raw.get("sway_pitch_range", 0.55) or 0.55),
            sway_rate_min_hz=float(speech_raw.get("sway_rate_min_hz", 0.7) or 0.7),
            sway_rate_max_hz=float(speech_raw.get("sway_rate_max_hz", 1.35) or 1.35),
            signature_scale_min=float(speech_raw.get("signature_scale_min", 0.82) or 0.82),
            signature_scale_max=float(speech_raw.get("signature_scale_max", 1.28) or 1.28),
            onset_emphasis_min=float(speech_raw.get("onset_emphasis_min", 0.55) or 0.55),
            onset_emphasis_max=float(speech_raw.get("onset_emphasis_max", 1.0) or 1.0),
            settle_strength_min=float(speech_raw.get("settle_strength_min", 0.38) or 0.38),
            settle_strength_max=float(speech_raw.get("settle_strength_max", 0.82) or 0.82),
            smile_boost_min=float(speech_raw.get("smile_boost_min", 0.02) or 0.02),
            smile_boost_max=float(speech_raw.get("smile_boost_max", 0.07) or 0.07),
            cooldown_falloff_ms=max(0, int(speech_raw.get("cooldown_falloff_ms", 760) or 760)),
            speaking_eye_x_range=float(speech_raw.get("speaking_eye_x_range", 0.28) or 0.28),
            speaking_eye_y_range=float(speech_raw.get("speaking_eye_y_range", 0.18) or 0.18),
            speaking_eye_anchor_x_range=float(speech_raw.get("speaking_eye_anchor_x_range", 0.12) or 0.12),
            speaking_eye_anchor_y_range=float(speech_raw.get("speaking_eye_anchor_y_range", 0.08) or 0.08),
            glance_rate_min_hz=float(speech_raw.get("glance_rate_min_hz", 0.55) or 0.55),
            glance_rate_max_hz=float(speech_raw.get("glance_rate_max_hz", 1.15) or 1.15),
            segment_duration_floor_ms=max(120, int(speech_raw.get("segment_duration_floor_ms", 680) or 680)),
            segment_duration_per_char_ms=float(speech_raw.get("segment_duration_per_char_ms", 64.0) or 64.0),
            accent_window_ratio_min=float(speech_raw.get("accent_window_ratio_min", 0.32) or 0.32),
            accent_window_ratio_max=float(speech_raw.get("accent_window_ratio_max", 0.74) or 0.74),
            accent_duration_ratio_min=float(speech_raw.get("accent_duration_ratio_min", 0.12) or 0.12),
            accent_duration_ratio_max=float(speech_raw.get("accent_duration_ratio_max", 0.26) or 0.26),
            accent_hold_ratio_min=float(speech_raw.get("accent_hold_ratio_min", 0.08) or 0.08),
            accent_hold_ratio_max=float(speech_raw.get("accent_hold_ratio_max", 0.20) or 0.20),
            accent_yaw_range=float(speech_raw.get("accent_yaw_range", 3.3) or 3.3),
            accent_pitch_range=float(speech_raw.get("accent_pitch_range", 1.45) or 1.45),
            accent_roll_range=float(speech_raw.get("accent_roll_range", 1.55) or 1.55),
            accent_eye_x_range=float(speech_raw.get("accent_eye_x_range", 0.50) or 0.50),
            accent_eye_y_range=float(speech_raw.get("accent_eye_y_range", 0.24) or 0.24),
            accent_switch_chance=float(speech_raw.get("accent_switch_chance", 0.42) or 0.42),
            accent_eye_lead_ratio=float(speech_raw.get("accent_eye_lead_ratio", 0.40) or 0.40),
            accent_head_follow_ratio=float(speech_raw.get("accent_head_follow_ratio", 0.72) or 0.72),
            accent_body_follow_ratio=float(speech_raw.get("accent_body_follow_ratio", 1.08) or 1.08),
            figure8_mix_min=float(speech_raw.get("figure8_mix_min", 0.08) or 0.08),
            figure8_mix_max=float(speech_raw.get("figure8_mix_max", 0.26) or 0.26),
            retarget_window_ratio_min=float(speech_raw.get("retarget_window_ratio_min", 0.42) or 0.42),
            retarget_window_ratio_max=float(speech_raw.get("retarget_window_ratio_max", 0.82) or 0.82),
            retarget_duration_ratio_min=float(speech_raw.get("retarget_duration_ratio_min", 0.16) or 0.16),
            retarget_duration_ratio_max=float(speech_raw.get("retarget_duration_ratio_max", 0.30) or 0.30),
            retarget_hold_ratio_min=float(speech_raw.get("retarget_hold_ratio_min", 0.10) or 0.10),
            retarget_hold_ratio_max=float(speech_raw.get("retarget_hold_ratio_max", 0.24) or 0.24),
            retarget_switch_chance=float(speech_raw.get("retarget_switch_chance", 0.58) or 0.58),
            retarget_eye_lead_ratio=float(speech_raw.get("retarget_eye_lead_ratio", 0.26) or 0.26),
            retarget_head_follow_ratio=float(speech_raw.get("retarget_head_follow_ratio", 0.56) or 0.56),
            retarget_body_follow_ratio=float(speech_raw.get("retarget_body_follow_ratio", 0.88) or 0.88),
            retarget_yaw_range=float(speech_raw.get("retarget_yaw_range", 2.8) or 2.8),
            retarget_pitch_range=float(speech_raw.get("retarget_pitch_range", 1.05) or 1.05),
            retarget_roll_range=float(speech_raw.get("retarget_roll_range", 1.2) or 1.2),
            retarget_eye_x_range=float(speech_raw.get("retarget_eye_x_range", 0.48) or 0.48),
            retarget_eye_y_range=float(speech_raw.get("retarget_eye_y_range", 0.22) or 0.22),
        ),
        baseline_profiles={str(key): str(value).strip() for key, value in baseline_raw.items() if str(value).strip()},
        metadata=_dict(raw.get("metadata")),
    )


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "service.toml"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float_map(value: Any) -> dict[str, float]:
    mapping = _dict(value)
    return {str(key): float(raw or 0.0) for key, raw in mapping.items()}


def _resolve_path(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else (repo_root / path).resolve()


if __name__ == "__main__":
    config = BodyServiceConfig.load(sys.argv[1] if len(sys.argv) > 1 else None)
    print(config)
