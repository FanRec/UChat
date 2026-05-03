from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from uchat.contracts import ShowProfile, normalize_show_profile


class ConfigError(RuntimeError):
    pass


SceneKind = Literal["console", "live_stream", "private_chat"]
LTMemMode = Literal["disabled", "optional", "required"]


@dataclass(frozen=True)
class RuntimeConfig:
    scene_id: str
    session_window_id: str
    locale: str
    identity: str
    audience_scope: str
    scene_kind: SceneKind


@dataclass(frozen=True)
class SceneDefaultsConfig:
    show_profile: ShowProfile = "free_talk"
    program_topic: str = ""
    segment_topic: str = ""
    micro_topic: str = ""
    danmaku_velocity: str = "n/a"
    audience_density: str = "n/a"
    risk_level: str = "L0"
    silence_level: str = "n/a"
    engagement_level: str = "n/a"


@dataclass(frozen=True)
class TimingGateConfig:
    priority_reply_threshold: float = 0.72
    paid_reply_threshold: float = 0.45
    risk_observe_only_threshold: float = 0.5
    busy_queue_depth_threshold: int = 3
    aggregated_show_value_threshold: float = 0.65
    dense_audience_reply_threshold: float = 0.55
    high_engagement_reply_threshold: float = 0.55
    cooling_keepalive_reply_threshold: float = 0.35
    default_reply_threshold: float = 0.45
    low_value_max_length: int = 1
    repeated_char_skip_min_length: int = 3
    low_value_literal_tokens: tuple[str, ...] = ("6", "66", "666", "dd", "1", "?", "？")


@dataclass(frozen=True)
class PromptConfig:
    root_dir: Path = Path("prompts")
    version: str = "v0"


@dataclass(frozen=True)
class LTMemConfig:
    base_url: str
    timeout_seconds: float
    mode: LTMemMode
    startup_healthcheck: bool
    use_cached_context_on_failure: bool
    heartbeat_interval_seconds: float

    @property
    def is_disabled(self) -> bool:
        return self.mode == "disabled"

    @property
    def is_optional(self) -> bool:
        return self.mode == "optional"

    @property
    def is_required(self) -> bool:
        return self.mode == "required"


@dataclass(frozen=True)
class DebugConfig:
    enabled: bool
    root_dir: Path
    save_llm_payloads: bool
    show_memory_context_in_console: bool
    console_view: Literal["timeline", "conversation", "minimal", "off"] = "timeline"
    show_full_pre_output_results: bool = True
    save_prompt_preview: bool = True
    artifact_name_mode: Literal["standard", "trace_prefixed"] = "standard"
    prompt_preview_chars: int = 1200
    memory_summary_chars: int = 280


@dataclass(frozen=True)
class LoggingConfig:
    log_dir: Path
    level: str
    console_level: str
    file_level: str
    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class ServiceConfig:
    url: str
    required: bool
    timeout_ms: int


@dataclass(frozen=True)
class IdentityConfig:
    store = Literal["memory", "sqlite"]
    store_type: str = "sqlite"
    sqlite_path: Path = Path("data/identity.sqlite3")
    default_console_person_id: str = ""


@dataclass(frozen=True)
class ServicesConfig:
    ltmem: ServiceConfig
    tts: ServiceConfig
    obs: ServiceConfig
    body: ServiceConfig
    platform: dict[str, ServiceConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    runtime: RuntimeConfig
    scene_defaults: SceneDefaultsConfig
    ltmem: LTMemConfig
    debug: DebugConfig
    logging: LoggingConfig
    timing_gate: TimingGateConfig = field(default_factory=TimingGateConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    services: ServicesConfig = field(
        default_factory=lambda: ServicesConfig(
            ltmem=ServiceConfig(url="http://127.0.0.1:8080", required=False, timeout_ms=30000),
            tts=ServiceConfig(url="http://127.0.0.1:8102", required=False, timeout_ms=3000),
            obs=ServiceConfig(url="http://127.0.0.1:8104", required=False, timeout_ms=3000),
            body=ServiceConfig(url="http://127.0.0.1:8103", required=False, timeout_ms=3000),
            platform={"bilibili": ServiceConfig(url="http://127.0.0.1:8110", required=False, timeout_ms=3000)},
        )
    )

    @classmethod
    def load(
        cls,
        *,
        app_config_path: str | Path = "config/app.toml",
        env: Mapping[str, str] | None = None,
    ) -> "Settings":
        _ = env or os.environ
        app_path = Path(app_config_path)
        app = _load_toml(app_path)

        runtime_raw = _section(app, "runtime", app_path)
        ltmem_raw = _section(app, "ltmem", app_path)
        debug_raw = _section(app, "debug", app_path)
        logging_raw = _section(app, "logging", app_path)
        prompt_raw = app.get("prompt", {})
        scene_defaults_raw = app.get("scene_defaults", {})
        timing_gate_raw = app.get("timing_gate", {})
        identity_raw = app.get("identity_store", {})
        services_raw = app.get("services", {})

        return cls(
            runtime=RuntimeConfig(
                scene_id=_str(runtime_raw, "scene_id", app_path),
                session_window_id=_str(runtime_raw, "session_window_id", app_path),
                locale=_str(runtime_raw, "locale", app_path),
                identity=_str(runtime_raw, "identity", app_path),
                audience_scope=_str(runtime_raw, "audience_scope", app_path),
                scene_kind=_scene_kind(runtime_raw, "scene_kind", app_path),
            ),
            scene_defaults=SceneDefaultsConfig(
                show_profile=normalize_show_profile(_str_default(scene_defaults_raw, "show_profile", "free_talk")),
                program_topic=_str_default(scene_defaults_raw, "program_topic", ""),
                segment_topic=_str_default(scene_defaults_raw, "segment_topic", ""),
                micro_topic=_str_default(scene_defaults_raw, "micro_topic", ""),
                danmaku_velocity=_str_default(scene_defaults_raw, "danmaku_velocity", "n/a"),
                audience_density=_str_default(scene_defaults_raw, "audience_density", "n/a"),
                risk_level=_str_default(scene_defaults_raw, "risk_level", "L0"),
                silence_level=_str_default(scene_defaults_raw, "silence_level", "n/a"),
                engagement_level=_str_default(scene_defaults_raw, "engagement_level", "n/a"),
            ),
            ltmem=LTMemConfig(
                base_url=_str(ltmem_raw, "base_url", app_path).rstrip("/"),
                timeout_seconds=_float(ltmem_raw, "timeout_seconds", app_path),
                mode=_ltmem_mode(ltmem_raw, "mode", app_path),
                startup_healthcheck=_bool(ltmem_raw, "startup_healthcheck", app_path),
                use_cached_context_on_failure=_bool(ltmem_raw, "use_cached_context_on_failure", app_path),
                heartbeat_interval_seconds=_float(ltmem_raw, "heartbeat_interval_seconds", app_path),
            ),
            debug=DebugConfig(
                enabled=_bool(debug_raw, "enabled", app_path),
                root_dir=Path(_str(debug_raw, "root_dir", app_path)),
                save_llm_payloads=_bool(debug_raw, "save_llm_payloads", app_path),
                show_memory_context_in_console=_bool(debug_raw, "show_memory_context_in_console", app_path),
                console_view=_console_view(debug_raw, "console_view", app_path),
                show_full_pre_output_results=_bool_default(debug_raw, "show_full_pre_output_results", True),
                save_prompt_preview=_bool_default(debug_raw, "save_prompt_preview", True),
                artifact_name_mode=_artifact_name_mode(debug_raw, "artifact_name_mode", "standard"),
                prompt_preview_chars=_int_default(debug_raw, "prompt_preview_chars", 1200),
                memory_summary_chars=_int_default(debug_raw, "memory_summary_chars", 280),
            ),
            logging=LoggingConfig(
                log_dir=Path(_str(logging_raw, "log_dir", app_path)),
                level=_str(logging_raw, "level", app_path),
                console_level=_str(logging_raw, "console_level", app_path),
                file_level=_str(logging_raw, "file_level", app_path),
                max_bytes=_int(logging_raw, "max_bytes", app_path),
                backup_count=_int(logging_raw, "backup_count", app_path),
            ),
            timing_gate=TimingGateConfig(
                priority_reply_threshold=_float_default(timing_gate_raw, "priority_reply_threshold", 0.72),
                paid_reply_threshold=_float_default(timing_gate_raw, "paid_reply_threshold", 0.45),
                risk_observe_only_threshold=_float_default(timing_gate_raw, "risk_observe_only_threshold", 0.5),
                busy_queue_depth_threshold=_int_default(timing_gate_raw, "busy_queue_depth_threshold", 3),
                aggregated_show_value_threshold=_float_default(timing_gate_raw, "aggregated_show_value_threshold", 0.65),
                dense_audience_reply_threshold=_float_default(timing_gate_raw, "dense_audience_reply_threshold", 0.55),
                high_engagement_reply_threshold=_float_default(timing_gate_raw, "high_engagement_reply_threshold", 0.55),
                cooling_keepalive_reply_threshold=_float_default(timing_gate_raw, "cooling_keepalive_reply_threshold", 0.35),
                default_reply_threshold=_float_default(timing_gate_raw, "default_reply_threshold", 0.45),
                low_value_max_length=_int_default(timing_gate_raw, "low_value_max_length", 1),
                repeated_char_skip_min_length=_int_default(timing_gate_raw, "repeated_char_skip_min_length", 3),
                low_value_literal_tokens=_str_tuple_default(
                    timing_gate_raw,
                    "low_value_literal_tokens",
                    ("6", "66", "666", "dd", "1", "?", "？"),
                ),
            ),
            identity=IdentityConfig(
                store_type=_identity_store_type(identity_raw, "store_type", "sqlite"),
                sqlite_path=Path(_str_default(identity_raw, "sqlite_path", "data/identity.sqlite3")),
                default_console_person_id=_str_default(identity_raw, "default_console_person_id", ""),
            ),
            prompt=PromptConfig(
                root_dir=Path(_str_default(prompt_raw, "root_dir", "prompts")),
                version=_str_default(prompt_raw, "version", "v0"),
            ),
            services=_build_services_config(
                services_raw=services_raw,
                ltmem_url=_str(ltmem_raw, "base_url", app_path).rstrip("/"),
                ltmem_required=_ltmem_mode(ltmem_raw, "mode", app_path) == "required",
                ltmem_timeout_ms=int(_float(ltmem_raw, "timeout_seconds", app_path) * 1000),
            ),
        )


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def _section(data: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ConfigError(f"missing [{key}] section in {path}")
    return value


def _str(data: Mapping[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"missing string key '{key}' in {path}")
    return value


def _bool(data: Mapping[str, Any], key: str, path: Path) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"missing boolean key '{key}' in {path}")
    return value


def _float(data: Mapping[str, Any], key: str, path: Path) -> float:
    value = data.get(key)
    if not isinstance(value, int | float):
        raise ConfigError(f"missing number key '{key}' in {path}")
    return float(value)


def _int(data: Mapping[str, Any], key: str, path: Path) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ConfigError(f"missing integer key '{key}' in {path}")
    return value


def _bool_default(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"invalid boolean key '{key}'")
    return value


def _int_default(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int):
        raise ConfigError(f"invalid integer key '{key}'")
    return value


def _float_default(data: Mapping[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if not isinstance(value, int | float):
        raise ConfigError(f"invalid number key '{key}'")
    return float(value)


def _ltmem_mode(data: Mapping[str, Any], key: str, path: Path) -> LTMemMode:
    value = _str(data, key, path)
    if value not in {"disabled", "optional", "required"}:
        raise ConfigError(f"invalid LTMem mode '{value}' in {path}")
    return value


def _console_view(
    data: Mapping[str, Any], key: str, path: Path
) -> Literal["timeline", "conversation", "minimal", "off"]:
    value = str(data.get(key, "timeline")).strip()
    if value not in {"timeline", "conversation", "minimal", "off"}:
        raise ConfigError(f"invalid console view '{value}' in {path}")
    return value  # type: ignore[return-value]


def _scene_kind(data: Mapping[str, Any], key: str, path: Path) -> SceneKind:
    value = _str(data, key, path)
    if value not in {"console", "live_stream", "private_chat"}:
        raise ConfigError(f"invalid scene kind '{value}' in {path}")
    return value  # type: ignore[return-value]


def _artifact_name_mode(
    data: Mapping[str, Any], key: str, default: str
) -> Literal["standard", "trace_prefixed"]:
    value = str(data.get(key, default)).strip()
    if value not in {"standard", "trace_prefixed"}:
        raise ConfigError(f"invalid artifact name mode '{value}'")
    return value  # type: ignore[return-value]


def _str_default(data: Mapping[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"invalid string key '{key}'")
    return value


def _identity_store_type(data: Mapping[str, Any], key: str, default: str) -> str:
    value = str(data.get(key, default)).strip()
    if value not in {"memory", "sqlite"}:
        raise ConfigError(f"invalid identity store type '{value}'")
    return value


def _str_tuple_default(data: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = data.get(key, list(default))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"invalid string list key '{key}'")
    return tuple(item for item in value if item.strip())


def _mapping_default(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"invalid section '{key}'")
    return value


def _build_service_config(
    data: Mapping[str, Any],
    *,
    default_url: str,
    default_required: bool,
    default_timeout_ms: int,
) -> ServiceConfig:
    return ServiceConfig(
        url=_str_default(data, "url", default_url).rstrip("/"),
        required=_bool_default(data, "required", default_required),
        timeout_ms=_int_default(data, "timeout_ms", default_timeout_ms),
    )


def _build_services_config(
    *,
    services_raw: Mapping[str, Any],
    ltmem_url: str,
    ltmem_required: bool,
    ltmem_timeout_ms: int,
) -> ServicesConfig:
    ltmem_raw = _mapping_default(services_raw, "ltmem")
    tts_raw = _mapping_default(services_raw, "tts")
    obs_raw = _mapping_default(services_raw, "obs")
    body_raw = _mapping_default(services_raw, "body")
    platform_raw = _mapping_default(services_raw, "platform")
    bilibili_raw = _mapping_default(platform_raw, "bilibili")
    return ServicesConfig(
        ltmem=_build_service_config(
            ltmem_raw,
            default_url=ltmem_url,
            default_required=ltmem_required,
            default_timeout_ms=ltmem_timeout_ms,
        ),
        tts=_build_service_config(
            tts_raw,
            default_url="http://127.0.0.1:8102",
            default_required=False,
            default_timeout_ms=3000,
        ),
        obs=_build_service_config(
            obs_raw,
            default_url="http://127.0.0.1:8104",
            default_required=False,
            default_timeout_ms=3000,
        ),
        body=_build_service_config(
            body_raw,
            default_url="http://127.0.0.1:8103",
            default_required=False,
            default_timeout_ms=3000,
        ),
        platform={
            "bilibili": _build_service_config(
                bilibili_raw,
                default_url="http://127.0.0.1:8110",
                default_required=False,
                default_timeout_ms=3000,
            )
        },
    )
