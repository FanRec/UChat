from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from uchat.config import LTMemConfig
from uchat.contracts import NormalizedEvent
from uchat.logging_utils import get_logger
from uchat.ltmem import LTMemError, LTMemGateway


@dataclass(frozen=True)
class MemoryHealthStatus:
    available: bool
    mode: str
    checked: bool
    recovered: bool = False
    error: str | None = None
    response: dict[str, Any] | None = None
    disabled: bool = False

    @property
    def required(self) -> bool:
        return self.mode == "required"


@dataclass(frozen=True)
class MemoryOperationResult:
    stage: str
    ok: bool
    available: bool
    degraded: bool
    recovered: bool
    status_label: str = "ok"
    silent: bool = False
    error: str | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    latency_ms: float | None = None
    fallback_source: str | None = None
    memory_context: str = ""


class MemoryOrchestrator:
    def __init__(self, config: LTMemConfig, gateway: LTMemGateway):
        self.config = config
        self.gateway = gateway
        self.available = not config.is_disabled
        self.last_error: str | None = None
        self._cached_context_by_session: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self.logger = get_logger("uchat.memory")

    def start(self) -> None:
        if not self.config.is_optional or self._heartbeat_thread is not None:
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="uchat-ltmem-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=max(self.config.heartbeat_interval_seconds * 2, 0.2))
        self.gateway.close()

    def startup_healthcheck(self) -> MemoryHealthStatus:
        if self.config.is_disabled:
            with self._lock:
                self.available = False
                self.last_error = "LTMem disabled by config"
            return MemoryHealthStatus(
                available=False,
                mode=self.config.mode,
                checked=False,
                error="LTMem disabled by config",
                disabled=True,
            )
        if not self.config.startup_healthcheck:
            return MemoryHealthStatus(available=self.available, mode=self.config.mode, checked=False)
        try:
            response = self.gateway.check_health()
        except LTMemError as exc:
            self._mark_unavailable(str(exc))
            return MemoryHealthStatus(
                available=False,
                mode=self.config.mode,
                checked=True,
                error=str(exc),
            )
        recovered = self._mark_available()
        return MemoryHealthStatus(
            available=True,
            mode=self.config.mode,
            checked=True,
            recovered=recovered,
            response=response,
        )

    def ingest_event(self, event: NormalizedEvent) -> MemoryOperationResult:
        if self.config.is_disabled:
            return self._disabled_result(stage="ltmem_ingest")
        if self.config.is_optional and not self.available:
            return self._unavailable_result(stage="ltmem_ingest")
        try:
            result = self.gateway.ingest_event(event)
        except LTMemError as exc:
            self._mark_unavailable(str(exc))
            return self._unavailable_result(stage="ltmem_ingest", error=str(exc))
        recovered = self._mark_available()
        return MemoryOperationResult(
            stage="ltmem_ingest",
            ok=True,
            available=True,
            degraded=False,
            recovered=recovered,
            status_label="ok",
            request=result.request,
            response=result.response,
            latency_ms=result.latency_ms,
        )

    def build_context_pack(
        self,
        *,
        query_text: str,
        scene_id: str,
        session_window_id: str,
        audience_scope: str,
        trace_id: str,
    ) -> MemoryOperationResult:
        if self.config.is_disabled:
            return self._context_fallback_result(
                session_window_id=session_window_id,
                stage="context_pack",
                error="LTMem disabled by config",
                degraded=False,
                fallback_source="disabled",
                status_label="disabled",
                silent=True,
            )
        if self.config.is_optional and not self.available:
            return self._context_fallback_result(
                session_window_id=session_window_id,
                stage="context_pack",
                error=self.last_error or "LTMem optional mode currently unavailable",
                degraded=True,
                status_label="skipped",
                silent=True,
            )
        try:
            result = self.gateway.build_context_pack(
                query_text=query_text,
                scene_id=scene_id,
                session_window_id=session_window_id,
                audience_scope=audience_scope,
                trace_id=trace_id,
            )
        except LTMemError as exc:
            self._mark_unavailable(str(exc))
            return self._context_fallback_result(
                session_window_id=session_window_id,
                stage="context_pack",
                error=str(exc),
                degraded=True,
                status_label="degraded",
                silent=False,
            )

        recovered = self._mark_available()
        memory_context = render_context_pack(result.response)
        with self._lock:
            self._cached_context_by_session[session_window_id] = memory_context
        return MemoryOperationResult(
            stage="context_pack",
            ok=True,
            available=True,
            degraded=False,
            recovered=recovered,
            status_label="ok",
            request=result.request,
            response=result.response,
            latency_ms=result.latency_ms,
            fallback_source="ltmem",
            memory_context=memory_context,
        )

    def ingest_assistant_reply(
        self,
        *,
        content: str,
        scene_id: str,
        session_window_id: str,
        reply_to_trace_id: str,
        trace_id: str,
    ) -> MemoryOperationResult:
        if self.config.is_disabled:
            return self._disabled_result(stage="assistant_reply_ingest")
        if self.config.is_optional and not self.available:
            return self._unavailable_result(stage="assistant_reply_ingest")
        try:
            result = self.gateway.ingest_assistant_reply(
                content=content,
                scene_id=scene_id,
                session_window_id=session_window_id,
                reply_to_trace_id=reply_to_trace_id,
                trace_id=trace_id,
            )
        except LTMemError as exc:
            self._mark_unavailable(str(exc))
            return self._unavailable_result(stage="assistant_reply_ingest", error=str(exc))
        recovered = self._mark_available()
        return MemoryOperationResult(
            stage="assistant_reply_ingest",
            ok=True,
            available=True,
            degraded=False,
            recovered=recovered,
            status_label="ok",
            request=result.request,
            response=result.response,
            latency_ms=result.latency_ms,
        )

    def _heartbeat_loop(self) -> None:
        interval = max(self.config.heartbeat_interval_seconds, 0.1)
        while not self._stop_event.wait(interval):
            if not self.config.is_optional or self.available:
                continue
            try:
                response = self.gateway.check_health()
            except LTMemError:
                continue
            self._mark_available()
            self.logger.info(
                f"LTMem heartbeat recovered: {response}",
                extra={"stage": "ltmem_heartbeat", "service": "ltmem", "status": "recovered", "degraded": False},
            )

    def _mark_available(self) -> bool:
        with self._lock:
            recovered = not self.available
            self.available = True
            self.last_error = None
        return recovered

    def _mark_unavailable(self, error: str) -> None:
        with self._lock:
            self.available = False
            self.last_error = error

    def _disabled_result(self, *, stage: str) -> MemoryOperationResult:
        return MemoryOperationResult(
            stage=stage,
            ok=False,
            available=False,
            degraded=False,
            recovered=False,
            status_label="disabled",
            silent=True,
            error="LTMem disabled by config",
            fallback_source="disabled",
        )

    def _unavailable_result(self, *, stage: str, error: str | None = None) -> MemoryOperationResult:
        return MemoryOperationResult(
            stage=stage,
            ok=False,
            available=False,
            degraded=True,
            recovered=False,
            status_label="skipped" if error is None else "degraded",
            silent=error is None,
            error=error or self.last_error or "LTMem optional mode currently unavailable",
        )

    def _context_fallback_result(
        self,
        *,
        session_window_id: str,
        stage: str,
        error: str,
        degraded: bool,
        fallback_source: str | None = None,
        status_label: str,
        silent: bool,
    ) -> MemoryOperationResult:
        with self._lock:
            cached_context = self._cached_context_by_session.get(session_window_id)
        if fallback_source != "disabled" and self.config.use_cached_context_on_failure and cached_context:
            return MemoryOperationResult(
                stage=stage,
                ok=False,
                available=False,
                degraded=degraded,
                recovered=False,
                status_label=status_label,
                silent=silent,
                error=error,
                fallback_source=fallback_source or "cached_context",
                memory_context=cached_context,
            )
        return MemoryOperationResult(
            stage=stage,
            ok=False,
            available=False,
            degraded=degraded,
            recovered=False,
            status_label=status_label,
            silent=silent,
            error=error,
            fallback_source=fallback_source or "empty_context",
            memory_context="",
        )


def render_context_pack(context_pack: dict[str, Any]) -> str:
    rendered = context_pack.get("rendered_context")
    if isinstance(rendered, str) and rendered.strip():
        return rendered
    blocks = context_pack.get("blocks")
    if blocks:
        return json.dumps(blocks, ensure_ascii=False, indent=2)
    return json.dumps(context_pack, ensure_ascii=False, indent=2)
