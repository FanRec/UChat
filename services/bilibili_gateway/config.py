from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from uchat.config import load_dotenv


class GatewayConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceSection:
    service_name: str
    base_url: str
    listen_host: str
    listen_port: int
    mock_mode: bool


@dataclass(frozen=True)
class RoomSection:
    room_id: str
    owner_uid: int = 0


@dataclass(frozen=True)
class AuthSection:
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""
    dedeuserid: str = ""

    def cookies(self) -> dict[str, str]:
        cookies: dict[str, str] = {}
        if self.sessdata:
            cookies["SESSDATA"] = self.sessdata
        if self.bili_jct:
            cookies["bili_jct"] = self.bili_jct
        if self.buvid3:
            cookies["buvid3"] = self.buvid3
        if self.dedeuserid:
            cookies["DedeUserID"] = self.dedeuserid
        return cookies


@dataclass(frozen=True)
class WindowSection:
    dedupe_window_ms: int
    burst_window_ms: int
    aggregate_window_ms: int
    scene_stats_window_ms: int


@dataclass(frozen=True)
class ComboSection:
    combo_quiet_timeout_ms: int
    milestone_counts: list[int] = field(default_factory=lambda: [1, 5, 10, 20, 50, 100])
    update_throttle_ms: int = 1500
    max_reply_candidates_per_combo: int = 2


@dataclass(frozen=True)
class QueueSection:
    max_events: int
    cursor_retention: int
    poll_default_limit: int


@dataclass(frozen=True)
class RiskSection:
    rule_file: str
    default_reply_policy: str


@dataclass(frozen=True)
class ObservabilitySection:
    log_level: str
    debug_dump_enabled: bool
    debug_dump_dir: str = "debug/bilibili_gateway"


@dataclass(frozen=True)
class TestingSection:
    offline_test_mode_enabled: bool
    live_connect_fallback_mode: str = ""


@dataclass(frozen=True)
class GatewayServiceConfig:
    service: ServiceSection
    room: RoomSection
    auth: AuthSection
    windows: WindowSection
    combo: ComboSection
    queue: QueueSection
    risk: RiskSection
    observability: ObservabilitySection
    testing: TestingSection


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_REPO_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_service_config(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> GatewayServiceConfig:
    config_path = Path(path) if path is not None else Path(__file__).resolve().parent / "config" / "service.toml"
    if not config_path.exists():
        raise GatewayConfigError(f"gateway config file not found: {config_path}")
    if env is None:
        load_dotenv(_REPO_ENV_PATH)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    expanded = _expand_env(raw, env or os.environ)
    service_raw = _section(expanded, "service", config_path)
    bilibili_raw = _section(expanded, "bilibili", config_path)
    gateway_raw = _section(expanded, "gateway", config_path)
    room_raw = _section(bilibili_raw, "room", config_path)
    auth_raw = _section(bilibili_raw, "auth", config_path)
    windows_raw = _section(gateway_raw, "windows", config_path)
    combo_raw = _section(gateway_raw, "combo", config_path)
    queue_raw = _section(gateway_raw, "queue", config_path)
    risk_raw = _section(gateway_raw, "risk", config_path)
    observability_raw = _section(gateway_raw, "observability", config_path)
    testing_raw = _section_optional(gateway_raw, "testing", config_path)
    return GatewayServiceConfig(
        service=ServiceSection(
            service_name=_required_str(service_raw, "service_name", config_path),
            base_url=_required_str(service_raw, "base_url", config_path).rstrip("/"),
            listen_host=_required_str(service_raw, "listen_host", config_path),
            listen_port=_required_int(service_raw, "listen_port", config_path),
            mock_mode=_required_bool(service_raw, "mock_mode", config_path),
        ),
        room=RoomSection(
            room_id=_required_str(room_raw, "room_id", config_path),
            owner_uid=_int_default(room_raw, "owner_uid", 0),
        ),
        auth=AuthSection(
            sessdata=_str_default(auth_raw, "sessdata", ""),
            bili_jct=_str_default(auth_raw, "bili_jct", ""),
            buvid3=_str_default(auth_raw, "buvid3", ""),
            dedeuserid=_str_default(auth_raw, "dedeuserid", ""),
        ),
        windows=WindowSection(
            dedupe_window_ms=_required_int(windows_raw, "dedupe_window_ms", config_path),
            burst_window_ms=_required_int(windows_raw, "burst_window_ms", config_path),
            aggregate_window_ms=_required_int(windows_raw, "aggregate_window_ms", config_path),
            scene_stats_window_ms=_required_int(windows_raw, "scene_stats_window_ms", config_path),
        ),
        combo=ComboSection(
            combo_quiet_timeout_ms=_required_int(combo_raw, "combo_quiet_timeout_ms", config_path),
            milestone_counts=_int_list(combo_raw.get("milestone_counts", [1, 5, 10, 20, 50, 100]), config_path, "milestone_counts"),
            update_throttle_ms=_required_int(combo_raw, "update_throttle_ms", config_path),
            max_reply_candidates_per_combo=_required_int(combo_raw, "max_reply_candidates_per_combo", config_path),
        ),
        queue=QueueSection(
            max_events=_required_int(queue_raw, "max_events", config_path),
            cursor_retention=_required_int(queue_raw, "cursor_retention", config_path),
            poll_default_limit=_required_int(queue_raw, "poll_default_limit", config_path),
        ),
        risk=RiskSection(
            rule_file=_str_default(risk_raw, "rule_file", ""),
            default_reply_policy=_required_str(risk_raw, "default_reply_policy", config_path),
        ),
        observability=ObservabilitySection(
            log_level=_required_str(observability_raw, "log_level", config_path),
            debug_dump_enabled=_required_bool(observability_raw, "debug_dump_enabled", config_path),
            debug_dump_dir=_str_default(observability_raw, "debug_dump_dir", "debug/bilibili_gateway"),
        ),
        testing=TestingSection(
            offline_test_mode_enabled=_bool_default(testing_raw, "offline_test_mode_enabled", False),
            live_connect_fallback_mode=_live_connect_fallback_mode(testing_raw.get("live_connect_fallback_mode", "")),
        ),
    )


def _expand_env(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item, env) for item in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: env.get(match.group(1), ""), value)
    return value


def _section(data: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise GatewayConfigError(f"missing section [{key}] in {path}")
    return value


def _section_optional(data: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GatewayConfigError(f"invalid section [{key}] in {path}")
    return value


def _required_str(data: Mapping[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GatewayConfigError(f"missing string key '{key}' in {path}")
    return value


def _required_int(data: Mapping[str, Any], key: str, path: Path) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise GatewayConfigError(f"missing integer key '{key}' in {path}")
    return value


def _required_bool(data: Mapping[str, Any], key: str, path: Path) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise GatewayConfigError(f"missing boolean key '{key}' in {path}")
    return value


def _str_default(data: Mapping[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise GatewayConfigError(f"invalid string key '{key}'")
    return value


def _int_default(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int):
        raise GatewayConfigError(f"invalid integer key '{key}'")
    return value


def _bool_default(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise GatewayConfigError(f"invalid boolean key '{key}'")
    return value


def _int_list(value: Any, path: Path, key: str) -> list[int]:
    if not isinstance(value, list) or any(not isinstance(item, int) for item in value):
        raise GatewayConfigError(f"invalid integer list '{key}' in {path}")
    return sorted({item for item in value if item > 0})


def _live_connect_fallback_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise GatewayConfigError("invalid string key 'live_connect_fallback_mode'")
    normalized = value.strip().lower()
    if normalized in {"", "offline_history"}:
        return normalized
    raise GatewayConfigError(
        "invalid live_connect_fallback_mode; expected '' or 'offline_history'"
    )
