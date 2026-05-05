from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from uchat.adapters import EmbodimentAdapter, OutputAdapter, TTSAdapter
from uchat.config import Settings
from uchat.console_view import truncate_text
from uchat.contracts import ContextPackView, NormalizedEvent, OutboundEvent, SessionState
from uchat.debug import DebugWriter
from uchat.identity import IdentityService
from uchat.memory import MemoryOrchestrator
from uchat.moderation import ModerationPipeline
from uchat.models import ModelRouter
from uchat.output_queue import OutputQueue
from uchat.prompts import PromptManager
from uchat.runtime.memory_stages import MemoryStageManager
from uchat.runtime.output import RuntimeOutputManager
from uchat.runtime.prompt_reply import PromptReplyPipeline
from uchat.runtime.scene_state import SceneStateUpdater
from uchat.runtime.timing_gate import TimingGate
from uchat.runtime.trace import MessageChainRecorder, RuntimeObserver
from uchat.logging_utils import get_logger, log_context


class SessionRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        memory: MemoryOrchestrator,
        model_router: ModelRouter,
        prompts: PromptManager,
        debug_writer: DebugWriter,
        timing_gate: TimingGate | None = None,
        scene_state_updater: SceneStateUpdater | None = None,
        observer: RuntimeObserver | None = None,
        output_adapter: OutputAdapter | None = None,
        tts_adapter: TTSAdapter | None = None,
        embodiment_adapter: EmbodimentAdapter | None = None,
        subtitle_adapter: OutputAdapter | None = None,
        output_queue: OutputQueue | None = None,
        identity_service: IdentityService | None = None,
        moderation_pipeline: ModerationPipeline | None = None,
    ):
        self.settings = settings
        self.memory = memory
        self.model_router = model_router
        self.prompts = prompts
        self.debug_writer = debug_writer
        self.timing_gate = timing_gate or TimingGate(settings.timing_gate)
        self.scene_state_updater = scene_state_updater or SceneStateUpdater(runtime=settings.runtime, defaults=settings.scene_defaults)
        self.state = SessionState(
            session_id=settings.runtime.session_window_id,
            scene_id=settings.runtime.scene_id,
            scene_state=self.scene_state_updater.initial_state(),
            short_history_render_window=settings.runtime.short_history_render_window,
            short_history_retention_limit=settings.runtime.short_history_retention_limit,
        )
        self.logger = get_logger("uchat.runtime")
        self.identity_service = identity_service or IdentityService()
        self.moderation_pipeline = moderation_pipeline or ModerationPipeline()
        self.output_queue = output_queue or OutputQueue()
        self.recorder = MessageChainRecorder(state=self.state, debug_writer=debug_writer, observer=observer)
        self.memory_stages = MemoryStageManager(
            settings=settings,
            state=self.state,
            recorder=self.recorder,
            logger=self.logger,
        )
        self.output_manager = RuntimeOutputManager(
            queue=self.output_queue,
            state=self.state,
            recorder=self.recorder,
            settings=settings,
            logger=self.logger,
            mark_degraded=self.memory_stages.mark_degraded,
            output_adapter=output_adapter,
            tts_adapter=tts_adapter,
            embodiment_adapter=embodiment_adapter,
            subtitle_adapter=subtitle_adapter,
        )
        self.turn_pipeline = PromptReplyPipeline(
            settings=settings,
            state=self.state,
            prompts=prompts,
            model_router=model_router,
            debug_writer=debug_writer,
            recorder=self.recorder,
            logger=self.logger,
        )

    def process_console_text(self, text: str) -> OutboundEvent | None:
        command = text.strip().lower()
        if command in {"/quit", "/exit"}:
            raise KeyboardInterrupt
        if not command:
            return None
        event = self._normalize_console_text(text)
        return self.process_event(event)

    def process_event(
        self,
        event: NormalizedEvent,
        *,
        pre_stages: list[Mapping[str, Any]] | None = None,
    ) -> OutboundEvent | None:
        self._prepare_turn_state()
        scene_state_before = self.state.scene_state.to_dict()
        self.scene_state_updater.apply_event(self.state.scene_state, event, session_state=self.state)
        scene_state_after = self.state.scene_state.to_dict()
        identity_context = self.identity_service.resolve_event_identity(event)
        context = log_context(trace_id=event.trace_id, session_id=event.session_window_id, scene_id=event.scene_id)
        message_chain = self.recorder.start_turn(event)
        self._record_pre_stages(event.trace_id, message_chain, pre_stages)
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="scene_state_update",
            service="runtime",
            status="ok",
            metrics={
                "active_platform": self.state.scene_state.active_platform,
                "danmaku_velocity": self.state.scene_state.danmaku_velocity,
                "audience_density": self.state.scene_state.audience_density,
                "risk_level": self.state.scene_state.risk_level,
                "silence_level": self.state.scene_state.silence_level,
                "engagement_level": self.state.scene_state.engagement_level,
            },
            result=MessageChainRecorder.result(
                kind="json",
                summary="scene state updated",
                full={
                    "before": scene_state_before,
                    "after": scene_state_after,
                },
                mime="application/json",
            ),
        )

        self._handle_tts_interrupt_gate(event=event, message_chain=message_chain)
        self._record_normalize_stage(event, context, message_chain)
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="identity_resolve",
            service="runtime",
            status=identity_context.identity_resolution or "unknown",
            metrics={
                "platform": identity_context.platform,
                "platform_nickname": identity_context.platform_nickname,
                "has_resolved_person_id": bool(identity_context.resolved_person_id),
                "has_display_name": bool(identity_context.display_name),
            },
            result=MessageChainRecorder.result(
                kind="json",
                summary="identity context resolved",
                full={
                    "resolved_person_id": identity_context.resolved_person_id,
                    "identity_resolution": identity_context.identity_resolution,
                    "display_name": identity_context.display_name,
                    "platform": identity_context.platform,
                    "platform_label": identity_context.platform_label,
                    "platform_nickname": identity_context.platform_nickname,
                },
                mime="application/json",
            ),
        )
        moderation_result = self.moderation_pipeline.evaluate(event)
        event.metadata["moderation_result"] = moderation_result.to_dict()
        event.metadata["reply_view_text"] = self.moderation_pipeline.reply_text(moderation_result, event.content_norm)
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="moderation",
            service="runtime",
            status=moderation_result.reply_policy,
            metrics={
                "moderation_label_count": len(moderation_result.moderation_labels),
                "quote_allowed": moderation_result.quote_allowed,
                "attack_intensity": moderation_result.attack_intensity,
                "target_scope": moderation_result.target_scope,
            },
            result=MessageChainRecorder.result(
                kind="json",
                summary="input moderation evaluated",
                full={
                    "raw_input_text": moderation_result.raw_input_text,
                    "reply_view_text": event.metadata["reply_view_text"],
                    "moderation_result": moderation_result.to_dict(),
                },
                mime="application/json",
            ),
        )

        memory_ingest = self.memory.ingest_event(event)
        if memory_ingest.request is not None:
            self.debug_writer.write(event.trace_id, "ltmem_ingest_request.json", memory_ingest.request)
        self.memory_stages.handle_result(
            trace_id=event.trace_id,
            context=context,
            message_chain=message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            result=memory_ingest,
            success_message="LTMem input ingest accepted",
            failure_message="LTMem input ingest degraded",
        )

        decision = self._run_timing_gate(event, context, message_chain)
        self.debug_writer.write(event.trace_id, "planner_decision.json", decision)
        if decision.action in {"skip", "quit", "wait", "listen_more", "observe_only"}:
            return self._finish_without_outbound(event, decision.action, message_chain)

        context_view = self._build_context_view(event, context, message_chain)
        identity_prompt_context = self.identity_service.render_identity_prompt_context(identity_context)
        prompt = self.turn_pipeline.render_prompt(
            event=event,
            context_view=context_view,
            identity_context=identity_context,
            identity_prompt_context=identity_prompt_context,
            message_chain=message_chain,
            queue_snapshot=self.output_manager.snapshot(),
        )
        llm_result = self.turn_pipeline.generate_reply(
            event=event,
            prompt=prompt,
            context=context,
            message_chain=message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            on_sentence=lambda chunk: self.output_manager.handle_sentence(
                trace_id=event.trace_id,
                text=chunk.text,
                message_chain=message_chain,
            ),
        )

        self.state.current_state = "speaking"
        self.state.remember_user(str(event.metadata.get("reply_view_text", event.content_norm)))
        self.state.remember_assistant(llm_result.text)
        self.state.remember_reply_latency(llm_result.latency_ms)

        outbound = OutboundEvent.console_text(trace_id=event.trace_id, text=llm_result.text)
        outbound.metadata["memory_context"] = context_view.text
        outbound.metadata["fallback_source"] = context_view.fallback_source or context_view.source or "empty_context"
        outbound.metadata["identity_context"] = dict(event.metadata.get("identity_context", {}))
        outbound.metadata["moderation_result"] = dict(event.metadata.get("moderation_result", {}))
        outbound.metadata["message_chain"] = message_chain
        self.debug_writer.write(event.trace_id, "outbound_event.json", outbound)

        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="output_dispatch",
            service="runtime",
            status="ok",
            metrics={"output_queue_depth": self.output_queue.depth, "channel": outbound.channel, "destination": outbound.destination},
            result=MessageChainRecorder.result(
                kind="text",
                summary=truncate_text(outbound.text, self.settings.debug.memory_summary_chars),
                full=outbound.text,
                mime="text/plain",
            ),
        )
        self.output_manager.dispatch_final_outbound(outbound, message_chain)

        reply_ingest = self.memory.ingest_assistant_reply(
            content=outbound.text,
            scene_id=event.scene_id,
            session_window_id=event.session_window_id,
            reply_to_trace_id=event.trace_id,
            trace_id=event.trace_id,
        )
        self.memory_stages.handle_result(
            trace_id=event.trace_id,
            context=context,
            message_chain=message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            result=reply_ingest,
            success_message="LTMem assistant reply ingest accepted",
            failure_message="LTMem assistant reply ingest degraded",
        )

        self.state.current_state = "observing_feedback"
        self.state.current_state = "idle"
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="turn_complete",
            service="runtime",
            status="ok",
            metrics={
                "short_history_size": len(self.state.short_history),
                "queue_high_watermark": self.output_queue.high_watermark,
            },
            result=MessageChainRecorder.result(kind="summary", summary="turn completed"),
        )
        self.recorder.finish_turn(message_chain, outbound)
        return outbound

    def _prepare_turn_state(self) -> None:
        self.state.current_state = "listening"
        self.state.degraded = False
        self.state.degraded_reason = ""

    def _handle_tts_interrupt_gate(self, *, event: NormalizedEvent, message_chain: dict[str, Any]) -> None:
        if not self.output_manager.has_active_tts_output():
            return
        if self._is_console_forced_interrupt(event):
            self.output_manager.interrupt_channel(trace_id=event.trace_id, message_chain=message_chain, channel="tts")
            return
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="interrupt_gate",
            service="runtime",
            status="skipped",
            decision="speaking_locked",
            metrics={
                "channel": "tts",
                "input_source": str(event.metadata.get("input_source", event.source_type)),
                "active_tts_in_flight": self.output_manager.active_tts_in_flight_count(),
            },
            result=MessageChainRecorder.result(
                kind="summary",
                summary="skipped TTS interrupt because speech is still in progress",
            ),
        )

    @staticmethod
    def _is_console_forced_interrupt(event: NormalizedEvent) -> bool:
        input_source = str(event.metadata.get("input_source", "")).strip().lower()
        return event.source_type == "console" or input_source == "console"

    def _normalize_console_text(self, text: str) -> NormalizedEvent:
        return NormalizedEvent.from_console(
            text=text,
            scene_id=self.settings.runtime.scene_id,
            session_window_id=self.settings.runtime.session_window_id,
        )

    def _record_normalize_stage(self, event: NormalizedEvent, context: dict[str, Any], message_chain: dict[str, Any]) -> None:
        self.logger.info(
            "normalized input event",
            extra=context | {"stage": "normalize", "service": "runtime", "status": "ok", "degraded": False},
        )
        self.debug_writer.write(event.trace_id, "normalized_event.json", event)
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="normalize",
            service="runtime",
            status="ok",
            result=MessageChainRecorder.result(kind="summary", summary=f"normalized input: {event.content_norm}"),
        )

    def _run_timing_gate(
        self,
        event: NormalizedEvent,
        context: dict[str, Any],
        message_chain: dict[str, Any],
    ):
        self.state.current_state = "timing_gate"
        decision = self.timing_gate.decide(event, session_state=self.state, output_queue=self.output_queue)
        self.logger.info(
            f"timing gate decision: {decision.action}",
            extra=context
            | {
                "stage": "timing_gate",
                "service": "runtime",
                "status": decision.action,
                "decision": decision.action,
                "degraded": self.state.degraded,
            },
        )
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="timing_gate",
            service="runtime",
            status=decision.action,
            decision=decision.action,
            metrics={"output_queue_depth": self.output_queue.depth},
            result=MessageChainRecorder.result(kind="summary", summary=decision.reasoning_summary),
        )
        return decision

    def _build_context_view(
        self,
        event: NormalizedEvent,
        context: dict[str, Any],
        message_chain: dict[str, Any],
    ) -> ContextPackView:
        self.state.current_state = "thinking"
        context_result = self.memory.build_context_pack(
            query_text=event.content_norm,
            scene_id=event.scene_id,
            session_window_id=event.session_window_id,
            audience_scope=self.settings.runtime.audience_scope,
            trace_id=event.trace_id,
        )
        if context_result.response is not None:
            self.debug_writer.write(event.trace_id, "context_pack.json", context_result.response)
        self.memory_stages.handle_result(
            trace_id=event.trace_id,
            context=context,
            message_chain=message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            result=context_result,
            success_message="LTMem context pack built",
            failure_message="LTMem context pack degraded",
            detail={"memory_context": context_result.memory_context},
        )
        return self.memory_stages.build_context_view(trace_id=event.trace_id, result=context_result)

    def _finish_without_outbound(
        self,
        event: NormalizedEvent,
        status: str,
        message_chain: dict[str, Any],
    ) -> OutboundEvent | None:
        self.recorder.record_stage(
            event.trace_id,
            message_chain,
            queue_snapshot=self.output_manager.snapshot(),
            stage="turn_complete",
            service="runtime",
            status=status,
            result=MessageChainRecorder.result(kind="summary", summary=f"turn completed without outbound: {status}"),
        )
        return None

    def _record_pre_stages(
        self,
        trace_id: str,
        message_chain: dict[str, Any],
        pre_stages: list[Mapping[str, Any]] | None,
    ) -> None:
        if not pre_stages:
            return
        for stage in pre_stages:
            self.recorder.record_stage(
                trace_id,
                message_chain,
                queue_snapshot=self.output_manager.snapshot(),
                stage=str(stage.get("stage", "pre_runtime")),
                service=str(stage.get("service", "runtime")),
                status=str(stage.get("status", "ok")),
                latency_ms=stage.get("latency_ms"),
                decision=str(stage["decision"]) if stage.get("decision") is not None else None,
                fallback_source=str(stage["fallback_source"]) if stage.get("fallback_source") is not None else None,
                error=str(stage["error"]) if stage.get("error") is not None else None,
                metrics=dict(stage.get("metrics") or {}),
                result=dict(stage.get("result") or MessageChainRecorder.result()),
            )
