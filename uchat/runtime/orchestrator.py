from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from uchat.adapters import EmbodimentAdapter, OutputAdapter, TTSAdapter
from uchat.config import RuntimeConfig, Settings
from uchat.contracts import NormalizedEvent, OutboundEvent, PrivacyScope
from uchat.debug import DebugWriter
from uchat.identity import IdentityService
from uchat.logging_utils import get_logger
from uchat.memory import MemoryOrchestrator
from uchat.models import ModelRouter
from uchat.output_queue import OutputQueue
from uchat.prompts import PromptManager
from uchat.runtime.scene_state import SceneStateUpdater
from uchat.runtime.session_runtime import SessionRuntime
from uchat.runtime.timing_gate import TimingGate
from uchat.runtime.trace import MessageChainRecorder, RuntimeObserver


AttentionRouteKind = str


@dataclass(frozen=True)
class AttentionRouteDecision:
    decision: AttentionRouteKind
    target_scene_id: str
    foreground_scene_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "target_scene_id": self.target_scene_id,
            "foreground_scene_id": self.foreground_scene_id,
            "reason": self.reason,
        }


@dataclass
class SceneRecord:
    scene_id: str
    scene_kind: str
    audience_scope: str
    privacy_scope: PrivacyScope
    runtime: SessionRuntime
    public_scene_digest: dict[str, Any]
    last_event_at: str | None = None
    is_foreground: bool = False

    def to_registry_entry(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "scene_kind": self.scene_kind,
            "audience_scope": self.audience_scope,
            "privacy_scope": self.privacy_scope,
            "is_foreground": self.is_foreground,
            "last_event_at": self.last_event_at,
            "public_scene_digest": dict(self.public_scene_digest),
        }


class SceneSupervisor:
    def __init__(self) -> None:
        self._records: dict[str, SceneRecord] = {}
        self._foreground_scene_id: str | None = None

    @property
    def foreground_scene_id(self) -> str | None:
        return self._foreground_scene_id

    def register_scene(self, record: SceneRecord, *, foreground: bool = False) -> SceneRecord:
        self._records[record.scene_id] = record
        if foreground or self._foreground_scene_id is None:
            self.ensure_foreground(record.scene_id)
        else:
            record.is_foreground = False
        return record

    def get_scene(self, scene_id: str) -> SceneRecord | None:
        return self._records.get(scene_id)

    def ensure_foreground(self, scene_id: str) -> SceneRecord:
        if scene_id not in self._records:
            raise KeyError(f"scene not registered: {scene_id}")
        previous = self._foreground_scene_id
        if previous is not None and previous in self._records:
            self._records[previous].is_foreground = False
        self._foreground_scene_id = scene_id
        record = self._records[scene_id]
        record.is_foreground = True
        return record

    def list_side_scenes(self) -> list[SceneRecord]:
        return [record for scene_id, record in self._records.items() if scene_id != self._foreground_scene_id]

    def update_scene_activity(self, scene_id: str, *, occurred_at: str) -> None:
        record = self._records.get(scene_id)
        if record is not None:
            record.last_event_at = occurred_at

    def update_public_scene_digest(self, scene_id: str, digest: dict[str, Any]) -> None:
        record = self._records.get(scene_id)
        if record is not None:
            record.public_scene_digest = digest

    def snapshot(self) -> dict[str, Any]:
        return {
            "foreground_scene_id": self._foreground_scene_id,
            "scenes": [record.to_registry_entry() for record in self._records.values()],
        }


class AttentionRouter:
    def route(self, event: NormalizedEvent, supervisor: SceneSupervisor) -> AttentionRouteDecision:
        foreground_scene_id = supervisor.foreground_scene_id
        if foreground_scene_id is None:
            return AttentionRouteDecision(
                decision="interrupt_now",
                target_scene_id=event.scene_id,
                foreground_scene_id=None,
                reason="no foreground scene yet",
            )
        if event.scene_id == foreground_scene_id:
            return AttentionRouteDecision(
                decision="interrupt_now",
                target_scene_id=event.scene_id,
                foreground_scene_id=foreground_scene_id,
                reason="event belongs to foreground scene",
            )
        if supervisor.get_scene(event.scene_id) is None:
            return AttentionRouteDecision(
                decision="background_handle",
                target_scene_id=event.scene_id,
                foreground_scene_id=foreground_scene_id,
                reason="new scene registered as side scene",
            )
        return AttentionRouteDecision(
            decision="background_handle",
            target_scene_id=event.scene_id,
            foreground_scene_id=foreground_scene_id,
            reason="side scene should not preempt foreground by default",
        )


class RuntimeOrchestrator:
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
        output_queue_factory: type[OutputQueue] = OutputQueue,
        attention_router: AttentionRouter | None = None,
        scene_supervisor: SceneSupervisor | None = None,
        identity_service: IdentityService | None = None,
    ) -> None:
        self.settings = settings
        self.memory = memory
        self.model_router = model_router
        self.prompts = prompts
        self.debug_writer = debug_writer
        self.timing_gate = timing_gate
        self.scene_state_updater = scene_state_updater
        self.observer = observer
        self.output_adapter = output_adapter
        self.tts_adapter = tts_adapter
        self.embodiment_adapter = embodiment_adapter
        self.subtitle_adapter = subtitle_adapter
        self.output_queue_factory = output_queue_factory
        self.attention_router = attention_router or AttentionRouter()
        self.scene_supervisor = scene_supervisor or SceneSupervisor()
        self.identity_service = identity_service or IdentityService()
        self.logger = get_logger("uchat.orchestrator")

        initial_runtime = self._create_session_runtime(
            runtime_config=settings.runtime,
            output_queue=self.output_queue_factory(),
        )
        initial_record = SceneRecord(
            scene_id=settings.runtime.scene_id,
            scene_kind=settings.runtime.scene_kind,
            audience_scope=settings.runtime.audience_scope,
            privacy_scope=self._privacy_scope_for_runtime(settings.runtime),
            runtime=initial_runtime,
            public_scene_digest=self._build_public_scene_digest(
                scene_id=settings.runtime.scene_id,
                runtime=initial_runtime,
                privacy_scope=self._privacy_scope_for_runtime(settings.runtime),
            ),
            is_foreground=True,
        )
        self.scene_supervisor.register_scene(initial_record, foreground=True)

    @property
    def foreground_scene_id(self) -> str | None:
        return self.scene_supervisor.foreground_scene_id

    def process_console_text(self, text: str) -> OutboundEvent | None:
        foreground_scene_id = self.scene_supervisor.foreground_scene_id
        if foreground_scene_id is None:
            raise RuntimeError("no foreground scene available")
        record = self.scene_supervisor.get_scene(foreground_scene_id)
        if record is None:
            raise RuntimeError(f"foreground scene missing: {foreground_scene_id}")
        return record.runtime.process_console_text(text)

    def process_event(self, event: NormalizedEvent) -> OutboundEvent | None:
        route = self.attention_router.route(event, self.scene_supervisor)
        record = self.scene_supervisor.get_scene(event.scene_id)
        if record is None:
            record = self._register_event_scene(event)
        self.scene_supervisor.update_scene_activity(event.scene_id, occurred_at=event.occurred_at)
        self._write_route_debug(event, route)

        if route.decision != "interrupt_now":
            self._write_registry_snapshot(event.trace_id)
            self.logger.info(
                f"attention routed event to {route.decision}",
                extra={
                    "trace_id": event.trace_id,
                    "session_id": event.session_window_id,
                    "scene_id": event.scene_id,
                    "foreground_scene_id": route.foreground_scene_id,
                    "target_scene_id": route.target_scene_id,
                    "stage": "attention_routing",
                    "service": "runtime",
                    "status": route.decision,
                    "decision": route.decision,
                    "route_decision": route.decision,
                    "scene_scope": record.privacy_scope,
                    "degraded": False,
                },
            )
            return None

        pre_stages = self._pre_stages_for(record, route)
        outbound = record.runtime.process_event(event, pre_stages=pre_stages)
        self.scene_supervisor.update_public_scene_digest(
            record.scene_id,
            self._build_public_scene_digest(
                scene_id=record.scene_id,
                runtime=record.runtime,
                privacy_scope=record.privacy_scope,
            ),
        )
        self._write_registry_snapshot(event.trace_id)
        return outbound

    def _register_event_scene(self, event: NormalizedEvent) -> SceneRecord:
        runtime_config = RuntimeConfig(
            scene_id=event.scene_id,
            session_window_id=event.session_window_id,
            locale=self.settings.runtime.locale,
            identity=self.settings.runtime.identity,
            audience_scope=self._audience_scope_for_event(event),
            scene_kind=self._scene_kind_for_event(event),
        )
        runtime = self._create_session_runtime(runtime_config=runtime_config, output_queue=self.output_queue_factory())
        privacy_scope = self._privacy_scope_for_event(event)
        record = SceneRecord(
            scene_id=event.scene_id,
            scene_kind=runtime_config.scene_kind,
            audience_scope=runtime_config.audience_scope,
            privacy_scope=privacy_scope,
            runtime=runtime,
            public_scene_digest=self._build_public_scene_digest(
                scene_id=event.scene_id,
                runtime=runtime,
                privacy_scope=privacy_scope,
            ),
        )
        foreground = self.scene_supervisor.foreground_scene_id is None
        return self.scene_supervisor.register_scene(record, foreground=foreground)

    def _create_session_runtime(self, *, runtime_config: RuntimeConfig, output_queue: OutputQueue) -> SessionRuntime:
        settings = replace(self.settings, runtime=runtime_config)
        return SessionRuntime(
            settings=settings,
            memory=self.memory,
            model_router=self.model_router,
            prompts=self.prompts,
            debug_writer=self.debug_writer,
            timing_gate=self.timing_gate,
            scene_state_updater=self.scene_state_updater,
            observer=self.observer,
            output_adapter=self.output_adapter,
            tts_adapter=self.tts_adapter,
            embodiment_adapter=self.embodiment_adapter,
            subtitle_adapter=self.subtitle_adapter,
            output_queue=output_queue,
            identity_service=self.identity_service,
        )

    def _pre_stages_for(self, record: SceneRecord, route: AttentionRouteDecision) -> list[dict[str, Any]]:
        scene_stage = {
            "stage": "scene_supervision",
            "service": "runtime",
            "status": "ok",
            "metrics": {
                "foreground_scene_id": self.scene_supervisor.foreground_scene_id,
                "target_scene_id": record.scene_id,
                "scene_scope": record.privacy_scope,
                "is_foreground": record.is_foreground,
            },
            "result": MessageChainRecorder.result(kind="summary", summary=f"scene prepared: {record.scene_id}"),
        }
        route_stage = {
            "stage": "attention_routing",
            "service": "runtime",
            "status": route.decision,
            "decision": route.decision,
            "metrics": {
                "foreground_scene_id": route.foreground_scene_id,
                "target_scene_id": route.target_scene_id,
                "scene_scope": record.privacy_scope,
            },
            "result": MessageChainRecorder.result(kind="summary", summary=route.reason),
        }
        return [scene_stage, route_stage]

    def _build_public_scene_digest(
        self,
        *,
        scene_id: str,
        runtime: SessionRuntime,
        privacy_scope: PrivacyScope,
    ) -> dict[str, Any]:
        scene_state = runtime.state.scene_state
        digest = {
            "scene_id": scene_id,
            "scene_kind": runtime.state.scene_state.scene_kind,
            "show_profile": scene_state.show_profile,
            "program_topic": scene_state.program_topic,
            "segment_topic": scene_state.segment_topic,
            "micro_topic": scene_state.micro_topic,
            "active_platform": scene_state.active_platform,
            "runtime_state": runtime.state.current_state,
            "privacy_scope": privacy_scope,
            "updated_at": runtime.state.last_input_at or runtime.state.last_reply_at,
        }
        if privacy_scope == "private":
            digest["program_topic"] = ""
            digest["segment_topic"] = ""
            digest["micro_topic"] = ""
        return digest

    def _write_route_debug(self, event: NormalizedEvent, route: AttentionRouteDecision) -> None:
        record = self.scene_supervisor.get_scene(event.scene_id)
        self.debug_writer.write(
            event.trace_id,
            "scene_route.json",
            {
                "trace_id": event.trace_id,
                "event_id": event.event_id,
                "scene_id": event.scene_id,
                "route": route.to_dict(),
                "scene_scope": record.privacy_scope if record is not None else None,
            },
        )

    def _write_registry_snapshot(self, trace_id: str) -> None:
        self.debug_writer.write(trace_id, "scene_registry_snapshot.json", self.scene_supervisor.snapshot())

    def _privacy_scope_for_runtime(self, runtime_config: RuntimeConfig) -> PrivacyScope:
        if runtime_config.audience_scope == "private":
            return "private"
        return "stream_safe" if runtime_config.scene_kind == "live_stream" else "public"

    def _privacy_scope_for_event(self, event: NormalizedEvent) -> PrivacyScope:
        if event.privacy_level >= 2:
            return "private"
        if str(event.metadata.get("platform", "")).strip() == "bilibili" or event.source_type.startswith("bilibili"):
            return "stream_safe"
        return "public"

    def _scene_kind_for_event(self, event: NormalizedEvent) -> str:
        if event.source_type.startswith("bilibili"):
            return "live_stream"
        if event.privacy_level >= 2:
            return "private_chat"
        return self.settings.runtime.scene_kind

    def _audience_scope_for_event(self, event: NormalizedEvent) -> str:
        if event.privacy_level >= 2:
            return "private"
        if event.source_type.startswith("bilibili"):
            return "stream"
        return self.settings.runtime.audience_scope
