from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from uchat.config import load_dotenv


class IdentityAdminConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceSection:
    service_name: str
    base_url: str
    listen_host: str
    listen_port: int


@dataclass(frozen=True)
class ObservabilitySection:
    log_level: str


@dataclass(frozen=True)
class IdentityAdminServiceConfig:
    service: ServiceSection
    observability: ObservabilitySection


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_REPO_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_service_config(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> IdentityAdminServiceConfig:
    config_path = Path(path) if path is not None else Path(__file__).resolve().parent / "config" / "service.toml"
    if not config_path.exists():
        raise IdentityAdminConfigError(f"identity_admin config file not found: {config_path}")
    if env is None:
        load_dotenv(_REPO_ENV_PATH)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)
    expanded = _expand_env(raw, env or os.environ)
    service_raw = _section(expanded, "service", config_path)
    observability_raw = _section(expanded, "observability", config_path)
    return IdentityAdminServiceConfig(
        service=ServiceSection(
            service_name=_required_str(service_raw, "service_name", config_path),
            base_url=_required_str(service_raw, "base_url", config_path).rstrip("/"),
            listen_host=_required_str(service_raw, "listen_host", config_path),
            listen_port=_required_int(service_raw, "listen_port", config_path),
        ),
        observability=ObservabilitySection(
            log_level=_required_str(observability_raw, "log_level", config_path),
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
        raise IdentityAdminConfigError(f"missing section [{key}] in {path}")
    return value


def _required_str(data: Mapping[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IdentityAdminConfigError(f"missing string key '{key}' in {path}")
    return value


def _required_int(data: Mapping[str, Any], key: str, path: Path) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise IdentityAdminConfigError(f"missing integer key '{key}' in {path}")
    return value
