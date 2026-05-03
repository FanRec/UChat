from __future__ import annotations

from typing import Any, Callable

from uchat.config import Settings
from uchat.console_view import truncate_text
from uchat.contracts import ContextPackView, NormalizedEvent, SessionState
from uchat.debug import DebugWriter
from uchat.identity import IdentityContext
from uchat.models import LLMStreamAggregate, LLMStreamEvent, ModelRouter
from uchat.prompts import PromptManager
from uchat.runtime.trace import MessageChainRecorder


class PromptReplyPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        state: SessionState,
        prompts: PromptManager,
        model_router: ModelRouter,
        debug_writer: DebugWriter,
        recorder: MessageChainRecorder,
        logger,
    ) -> None:
        self.settings = settings
        self.state = state
        self.prompts = prompts
        self.model_router = model_router
        self.debug_writer = debug_writer
        self.recorder = recorder
        self.logger = logger

    def render_prompt(
        self,
        *,
        event: NormalizedEvent,
        context_view: ContextPackView,
        identity_context: IdentityContext,
        identity_prompt_context: str,
        message_chain: dict[str, Any],
        queue_snapshot: dict[str, int],
    ) -> str:
        resolved = self.model_router.resolve("replyer")
        short_history_self_view = self.state.render_short_history_self_view()
        prompt = self.prompts.render(
            "replyer",
            identity=self.settings.runtime.identity,
            short_history=short_history_self_view,
            context_pack=context_view.text,
            identity_context=identity_prompt_context,
            user_input=str(event.metadata.get("reply_view_text", event.content_norm)),
        )
        prompt_preview = prompt[: self.settings.debug.prompt_preview_chars]
        if self.settings.debug.save_prompt_preview:
            self.debug_writer.write_text(event.trace_id, "prompt_preview.txt", prompt_preview)
            self.debug_writer.write(
                event.trace_id,
                "prompt_preview_meta.json",
                {
                    "prompt_name": "replyer",
                    "prompt_version": self.prompts.version,
                    "prompt_path": str(self.prompts.resolve_path("replyer")),
                    "preview_chars": self.settings.debug.prompt_preview_chars,
                    "model_route": resolved.route_label,
                    "profile_id": resolved.profile.profile_id,
                },
            )
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage="prompt_rendered",
            service="runtime",
            status="ok",
            metrics={
                "chars": len(prompt),
                "history_turns": len(self.state.short_history),
                "history_render_mode": "self_view",
                "context_chars": len(context_view.text),
                "identity_resolution": identity_context.identity_resolution,
                "has_display_name": bool(identity_context.display_name),
                "prompt_version": self.prompts.version,
                "model_route": resolved.route_label,
            },
            result=MessageChainRecorder.result(
                kind="summary",
                summary=f"prompt prepared for {resolved.route_label}",
            ),
        )
        return prompt

    def generate_reply(
        self,
        *,
        event: NormalizedEvent,
        prompt: str,
        context: dict[str, Any],
        message_chain: dict[str, Any],
        queue_snapshot: dict[str, int],
        on_sentence: Callable[[LLMStreamEvent], None] | None = None,
    ) -> LLMStreamAggregate:
        resolved = self.model_router.resolve("replyer")
        client = self.model_router.client_for_role("replyer")
        self.logger.info(
            "reply generation started",
            extra=context
            | {
                "stage": "plan_or_reply",
                "service": "llm",
                "status": "started",
                "streaming": True,
                "prompt_version": self.prompts.version,
                "metrics": {"model_route": resolved.route_label},
            },
        )
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage="plan_or_reply",
            service="llm",
            status="started",
            metrics={"streaming": True, "model_route": resolved.route_label},
            result=MessageChainRecorder.result(
                kind="summary",
                summary=f"streaming reply started with {resolved.route_label}",
            ),
        )
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage="llm_streaming",
            service="llm",
            status="started",
            metrics={"streaming": True, "model_route": resolved.route_label},
            result=MessageChainRecorder.result(
                kind="summary",
                summary=f"streaming reply started with {resolved.route_label}",
            ),
        )
        try:
            llm_result = client.aggregate_stream(
                prompt=prompt,
                trace_id=event.trace_id,
                on_event=self._event_handler(event.trace_id, on_sentence),
            )
        except Exception as exc:
            self.recorder.record_stage(
                event.trace_id,
                message_chain,
                queue_snapshot=queue_snapshot,
                stage="plan_or_reply",
                service="llm",
                status="error",
                error=str(exc),
                result=MessageChainRecorder.result(kind="summary", summary="llm streaming failed"),
            )
            raise

        if self.settings.debug.save_llm_payloads:
            self.debug_writer.write(event.trace_id, "llm_request.json", llm_result.request)
            self.debug_writer.write(event.trace_id, "llm_stream_events.json", llm_result.response_events)
            self.debug_writer.write(
                event.trace_id,
                "llm_stream_summary.json",
                {
                    "text": llm_result.text,
                    "latency_ms": llm_result.latency_ms,
                    "first_token_latency_ms": llm_result.first_token_latency_ms,
                    "first_sentence_latency_ms": llm_result.first_sentence_latency_ms,
                    "delta_count": llm_result.delta_count,
                    "sentence_count": llm_result.sentence_count,
                    "model_route": resolved.route_label,
                },
            )
        self.logger.info(
            "reply generation completed",
            extra=context
            | {
                "stage": "plan_or_reply",
                "service": "llm",
                "status": "ok",
                "latency_ms": round(llm_result.latency_ms, 2),
                "first_token_latency_ms": llm_result.first_token_latency_ms,
                "first_sentence_latency_ms": llm_result.first_sentence_latency_ms,
                "streaming": True,
                "degraded": self.state.degraded,
                "metrics": {"model_route": resolved.route_label},
            },
        )
        result_payload = MessageChainRecorder.result(
            kind="text",
            summary=truncate_text(llm_result.text, self.settings.debug.memory_summary_chars),
            full=llm_result.text,
            mime="text/plain",
        )
        metrics = {
            "streaming": True,
            "first_token_latency_ms": llm_result.first_token_latency_ms,
            "first_sentence_latency_ms": llm_result.first_sentence_latency_ms,
            "delta_count": llm_result.delta_count,
            "sentence_count": llm_result.sentence_count,
            "model_route": resolved.route_label,
            "profile_id": resolved.profile.profile_id,
        }
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage="plan_or_reply",
            service="llm",
            status="ok",
            latency_ms=round(llm_result.latency_ms, 2),
            metrics=metrics,
            result=result_payload,
        )
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=queue_snapshot,
            stage="llm_stream_completed",
            service="llm",
            status="ok",
            latency_ms=round(llm_result.latency_ms, 2),
            metrics=metrics,
            result=result_payload,
        )
        return llm_result

    def _event_handler(
        self,
        trace_id: str,
        on_sentence: Callable[[LLMStreamEvent], None] | None,
    ) -> Callable[[LLMStreamEvent], None]:
        def handle(event: LLMStreamEvent) -> None:
            if event.event_type != "sentence":
                return
            if on_sentence is not None:
                on_sentence(event)
            else:
                self.recorder.record_llm_event(trace_id, event, 0)

        return handle
