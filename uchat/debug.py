from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from uchat.config import DebugConfig


SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "x-api-key",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
}


class DebugWriter:
    def __init__(self, config: DebugConfig):
        self.config = config

    def write(self, trace_id: str, filename: str, payload: Any) -> None:
        if not self.config.enabled:
            return
        target = self.path_for(trace_id, filename)
        target.write_text(
            json.dumps(sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def write_text(self, trace_id: str, filename: str, content: str) -> None:
        if not self.config.enabled:
            return
        target = self.path_for(trace_id, filename)
        target.write_text(content, encoding="utf-8")

    def path_for(self, trace_id: str, filename: str) -> Path:
        trace_dir = self.config.root_dir / trace_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        resolved_name = self._artifact_name(trace_id, filename)
        return trace_dir / resolved_name

    def _artifact_name(self, trace_id: str, filename: str) -> str:
        if self.config.artifact_name_mode == "trace_prefixed":
            return f"{trace_id}__{filename}"
        return filename


def sanitize(value: Any) -> Any:
    if is_dataclass(value):
        return sanitize(asdict(value))
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                clean[key] = "<redacted>"
            else:
                clean[key] = sanitize(item)
        return clean
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
