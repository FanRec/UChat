from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from rich.console import Console
from rich.text import Text

from uchat.config import LoggingConfig
from uchat.debug import sanitize


_CONSOLE = Console(stderr=True)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "trace_id",
            "session_id",
            "scene_id",
            "foreground_scene_id",
            "target_scene_id",
            "stage",
            "service",
            "latency_ms",
            "queue_wait_ms",
            "first_token_latency_ms",
            "first_sentence_latency_ms",
            "status",
            "decision",
            "route_decision",
            "degraded",
            "scene_scope",
            "policy_version",
            "fallback_source",
            "streaming",
            "task_id",
            "target",
            "channel",
            "destination",
            "prompt_version",
            "error",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        metrics = getattr(record, "metrics", None)
        if metrics is not None:
            payload["metrics"] = metrics
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(sanitize(payload), ensure_ascii=False, sort_keys=True)


class ConsoleLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "trace_id", None):
                return
            _CONSOLE.print(self.format_line(record))
        except Exception:
            self.handleError(record)

    @staticmethod
    def format_line(record: logging.LogRecord) -> Text:
        parts = [f"[{record.levelname}]"]
        stage = getattr(record, "stage", None)
        service = getattr(record, "service", None)
        status = getattr(record, "status", None)
        if stage:
            parts.append(str(stage))
        if service:
            parts.append(str(service))
        if status:
            parts.append(str(status))
        parts.append(record.getMessage())
        return Text(" | ".join(parts), style=_text_style(record))


def configure_logging(config: LoggingConfig) -> None:
    root = logging.getLogger()
    root.setLevel(_level(config.level))
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    console = ConsoleLogHandler()
    console.setLevel(_level(config.console_level))
    root.addHandler(console)

    config.log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.log_dir / "uchat.log.jsonl",
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(_level(config.file_level))
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)
    _quiet_library_loggers()


def get_logger(module_name: str) -> logging.Logger:
    return logging.getLogger(module_name)


def log_context(
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    scene_id: str | None = None,
    stage: str | None = None,
    service: str | None = None,
    latency_ms: float | None = None,
    first_token_latency_ms: float | None = None,
    first_sentence_latency_ms: float | None = None,
    status: str | None = None,
    decision: str | None = None,
    degraded: bool | None = None,
    fallback_source: str | None = None,
    streaming: bool | None = None,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "scene_id": scene_id,
        "stage": stage,
        "service": service,
        "latency_ms": latency_ms,
        "first_token_latency_ms": first_token_latency_ms,
        "first_sentence_latency_ms": first_sentence_latency_ms,
        "status": status,
        "decision": decision,
        "degraded": degraded,
        "fallback_source": fallback_source,
        "streaming": streaming,
    }


def _level(value: str) -> int:
    return getattr(logging, value.upper(), logging.INFO)


def _text_style(record: logging.LogRecord) -> str:
    if record.levelno >= logging.ERROR:
        return "bold red"
    if str(getattr(record, "status", "")).lower() == "degraded":
        return "yellow"
    if str(getattr(record, "status", "")).lower() == "recovered":
        return "green"
    return "white"


def _quiet_library_loggers() -> None:
    for logger_name in (
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "aiohttp.access",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
