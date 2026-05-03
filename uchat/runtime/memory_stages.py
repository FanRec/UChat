from __future__ import annotations

from typing import Any

from uchat.config import Settings
from uchat.console_view import truncate_text
from uchat.contracts import ContextPackView, SessionState
from uchat.memory import MemoryOperationResult
from uchat.runtime.trace import MessageChainRecorder


class MemoryStageManager:
    def __init__(
        self,
        *,
        settings: Settings,
        state: SessionState,
        recorder: MessageChainRecorder,
        logger,
    ) -> None:
        self.settings = settings
        self.state = state
        self.recorder = recorder
        self.logger = logger

    def build_context_view(self, *, trace_id: str, result: MemoryOperationResult) -> ContextPackView:
        return ContextPackView(
            trace_id=trace_id,
            text=result.memory_context,
            source="ltmem" if result.ok else (result.fallback_source or "empty_context"),
            fallback_source=result.fallback_source,
            metadata={"raw_stage": result.stage},
        )

    def handle_result(
        self,
        *,
        trace_id: str,
        context: dict[str, Any],
        message_chain: dict[str, Any],
        queue_snapshot: dict[str, int],
        result: MemoryOperationResult,
        success_message: str,
        failure_message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        stage_name = self.stage_name(result.stage)
        if result.ok:
            self.logger.info(
                success_message,
                extra=context
                | {
                    "stage": stage_name,
                    "service": "ltmem",
                    "status": "ok",
                    "latency_ms": round(result.latency_ms or 0.0, 2),
                    "degraded": self.state.degraded,
                    "fallback_source": result.fallback_source,
                },
            )
            self.recorder.record_stage(
                trace_id,
                message_chain,
                queue_snapshot=queue_snapshot,
                stage=stage_name,
                service="ltmem",
                status="ok",
                latency_ms=round(result.latency_ms or 0.0, 2),
                fallback_source=result.fallback_source,
                metrics=self.memory_metrics(result),
                result=self.memory_result(result, detail),
            )
            self.record_legacy_stage(
                trace_id=trace_id,
                message_chain=message_chain,
                queue_snapshot=queue_snapshot,
                result=result,
                stage_name=stage_name,
                status="ok",
                detail=detail,
            )
            if result.recovered:
                self.logger.info(
                    "LTMem recovered",
                    extra=context | {"stage": "recovered", "service": "ltmem", "status": "recovered", "degraded": False},
                )
                self.recorder.record_stage(
                    trace_id,
                    message_chain,
                    queue_snapshot=queue_snapshot,
                    stage="recovered",
                    service="ltmem",
                    status="recovered",
                    result=MessageChainRecorder.result(kind="summary", summary=f"ltmem recovered after {stage_name}"),
                )
            return

        if result.silent:
            if result.degraded:
                self.mark_degraded(result.error or failure_message)
            self.recorder.record_stage(
                trace_id,
                message_chain,
                queue_snapshot=queue_snapshot,
                stage=stage_name,
                service="ltmem",
                status=result.status_label,
                fallback_source=result.fallback_source,
                error=result.error,
                metrics=self.memory_metrics(result),
                result=self.memory_result(result, detail),
            )
            self.record_legacy_stage(
                trace_id=trace_id,
                message_chain=message_chain,
                queue_snapshot=queue_snapshot,
                result=result,
                stage_name=stage_name,
                status=result.status_label,
                detail=detail,
                error=result.error,
            )
            return

        if not result.degraded:
            self.logger.info(
                result.error or "LTMem disabled",
                extra=context
                | {
                    "stage": stage_name,
                    "service": "ltmem",
                    "status": "disabled",
                    "degraded": False,
                    "fallback_source": result.fallback_source,
                },
            )
            self.recorder.record_stage(
                trace_id,
                message_chain,
                queue_snapshot=queue_snapshot,
                stage=stage_name,
                service="ltmem",
                status="disabled",
                fallback_source=result.fallback_source,
                error=result.error,
                metrics=self.memory_metrics(result),
                result=self.memory_result(result, detail),
            )
            self.record_legacy_stage(
                trace_id=trace_id,
                message_chain=message_chain,
                queue_snapshot=queue_snapshot,
                result=result,
                stage_name=stage_name,
                status="disabled",
                detail=detail,
                error=result.error,
            )
            return

        self.mark_degraded(result.error or failure_message)
        self.logger.warning(
            f"{failure_message}: {result.error}",
            extra=context
            | {
                "stage": stage_name,
                "service": "ltmem",
                "status": "degraded",
                "degraded": True,
                "fallback_source": result.fallback_source,
            },
        )
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage=stage_name,
            service="ltmem",
            status="degraded",
            fallback_source=result.fallback_source,
            error=result.error,
            metrics=self.memory_metrics(result),
            result=self.memory_result(result, detail),
        )
        self.record_legacy_stage(
            trace_id=trace_id,
            message_chain=message_chain,
            queue_snapshot=queue_snapshot,
            result=result,
            stage_name=stage_name,
            status="degraded",
            detail=detail,
            error=result.error,
        )
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage="degraded",
            service="ltmem",
            status="degraded",
            fallback_source=result.fallback_source,
            error=result.error,
            result=MessageChainRecorder.result(kind="summary", summary=f"continuing in degraded mode after {stage_name}"),
        )

    def mark_degraded(self, reason: str) -> None:
        self.state.degraded = True
        if not self.state.degraded_reason:
            self.state.degraded_reason = reason

    def memory_metrics(self, result: MemoryOperationResult) -> dict[str, Any]:
        return {
            "available": result.available,
            "recovered": result.recovered,
        }

    def memory_result(self, result: MemoryOperationResult, detail: dict[str, Any] | None) -> dict[str, Any]:
        if result.stage == "context_pack":
            summary = result.fallback_source or result.status_label
            if result.memory_context:
                summary = truncate_text(result.memory_context, self.settings.debug.memory_summary_chars)
            return MessageChainRecorder.result(
                kind="text",
                summary=summary,
                full=result.memory_context or None,
                mime="text/plain",
            )
        if result.response is not None:
            return MessageChainRecorder.result(
                kind="json",
                summary=f"{result.stage} response recorded",
                full=MessageChainRecorder.json_text(result.response),
                mime="application/json",
            )
        if detail and detail.get("memory_context"):
            return MessageChainRecorder.result(
                kind="text",
                summary=truncate_text(str(detail["memory_context"]), self.settings.debug.memory_summary_chars),
                full=str(detail["memory_context"]),
                mime="text/plain",
            )
        return MessageChainRecorder.result(kind="summary", summary=result.error or result.fallback_source or result.status_label)

    def record_legacy_stage(
        self,
        *,
        trace_id: str,
        message_chain: dict[str, Any],
        queue_snapshot: dict[str, int],
        result: MemoryOperationResult,
        stage_name: str,
        status: str,
        detail: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if stage_name == result.stage:
            return
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage=result.stage,
            service="ltmem",
            status=status,
            latency_ms=round(result.latency_ms or 0.0, 2) if result.latency_ms is not None else None,
            fallback_source=result.fallback_source,
            error=error,
            metrics=self.memory_metrics(result),
            result=self.memory_result(result, detail),
        )

    @staticmethod
    def stage_name(stage: str) -> str:
        return {
            "ltmem_ingest": "memory_ingest",
            "context_pack": "context_build",
            "assistant_reply_ingest": "assistant_writeback",
        }.get(stage, stage)
