from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from uchat.contracts.gateway import GatewayInputEvent


EventKind = Literal["speech", "vision", "audio", "tool", "system", "world", "action", "feedback"]
PrivacyScope = Literal["public", "stream_safe", "private"]
DecisionAction = Literal[
    "reply",
    "wait",
    "skip",
    "listen_more",
    "force_continue",
    "observe_only",
    "candidate_intervene",
    "proactive_continue",
    "ask_clarification",
    "call_tool",
    "continue_thinking",
    "handoff",
    "emergency_stop",
    "quit",
]
OutputTaskStatus = Literal["pending", "in_progress", "completed", "cancelled", "failed"]
DeliveryStatus = Literal["delivered", "cancelled", "failed", "skipped"]
OutputSegmentKind = Literal["sentence", "final", "notice"]
OutputChannel = Literal["text", "tts", "subtitle", "status", "body"]
OutputDestination = Literal["console", "bilibili", "obs", "tts_service", "body_service"]
ShowProfile = Literal[
    "free_talk",
    "singing",
    "gaming",
    "collab",
    "creation",
    "special_project",
    "outdoor",
    "new_outfit",
    "commemoration",
    "endurance_stream",
]

SHOW_PROFILE_DISPLAY_NAMES: dict[ShowProfile, str] = {
    "free_talk": "杂谈",
    "singing": "歌回",
    "gaming": "游戏",
    "collab": "联动",
    "creation": "创作",
    "special_project": "企划",
    "outdoor": "户外",
    "new_outfit": "新衣装",
    "commemoration": "纪念",
    "endurance_stream": "耐久直播",
}


def new_trace_id() -> str:
    return f"trace_{uuid4().hex[:16]}"


def new_event_id() -> str:
    return f"evt_{uuid4().hex[:16]}"


def new_decision_id() -> str:
    return f"dec_{uuid4().hex[:16]}"


def new_outbound_id() -> str:
    return f"out_{uuid4().hex[:16]}"


def new_segment_id() -> str:
    return f"seg_{uuid4().hex[:16]}"


def new_task_id() -> str:
    return f"task_{uuid4().hex[:16]}"


def new_receipt_id() -> str:
    return f"rcpt_{uuid4().hex[:16]}"


def new_command_id() -> str:
    return f"body_{uuid4().hex[:16]}"


def new_tool_call_id() -> str:
    return f"tool_{uuid4().hex[:16]}"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_show_profile(value: str) -> ShowProfile:
    normalized = value.strip()
    if normalized not in SHOW_PROFILE_DISPLAY_NAMES:
        raise ValueError(f"invalid show_profile '{value}'")
    return normalized  # type: ignore[return-value]


@dataclass
class SceneState:
    scene_kind: str
    audience_scope: str
    show_profile: ShowProfile = "free_talk"
    program_topic: str = ""
    segment_topic: str = ""
    micro_topic: str = ""
    danmaku_velocity: str = "n/a"
    audience_density: str = "n/a"
    risk_level: str = "L0"
    silence_level: str = "n/a"
    engagement_level: str = "n/a"
    active_platform: str = "console"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["show_profile_display_name"] = SHOW_PROFILE_DISPLAY_NAMES[self.show_profile]
        return payload


@dataclass
class SessionStateSnapshot:
    current_output_occupancy: int = 0
    last_interrupt_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedEvent:
    event_id: str
    trace_id: str
    occurred_at: str
    event_kind: EventKind
    source_type: str
    scene_id: str
    session_window_id: str
    content_raw: str
    content_norm: str
    speaker_candidates: list[dict[str, Any]] = field(default_factory=list)
    binding_evidence: list[dict[str, Any]] = field(default_factory=list)
    binding_confidence: float = 0.0
    target_kind: str = "unknown"
    target_entity_id: str = ""
    target_confidence: float = 0.0
    resolved_person_id: str = ""
    identity_resolution: str = "unknown"
    message_value: dict[str, Any] = field(default_factory=dict)
    aggregation_info: dict[str, Any] = field(default_factory=dict)
    moderation_view: dict[str, Any] = field(default_factory=dict)
    intervention_candidate: bool = False
    intervention_reason: str = ""
    emotion_context: dict[str, Any] = field(default_factory=dict)
    tool_context: dict[str, Any] = field(default_factory=dict)
    privacy_level: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_console(cls, *, text: str, scene_id: str, session_window_id: str) -> "NormalizedEvent":
        trace_id = new_trace_id()
        return cls(
            event_id=new_event_id(),
            trace_id=trace_id,
            occurred_at=utc_now_iso(),
            event_kind="speech",
            source_type="console",
            scene_id=scene_id,
            session_window_id=session_window_id,
            content_raw=text,
            content_norm=text.strip(),
            speaker_candidates=[
                {
                    "entity_id": "console:user_local_default",
                    "speaker_role": "user",
                    "confidence": 1.0,
                    "source": "console",
                }
            ],
            binding_evidence=[
                {
                    "evidence_type": "console_session",
                    "value": session_window_id,
                    "confidence": 1.0,
                }
            ],
            binding_confidence=1.0,
            target_kind="unknown",
            target_entity_id="",
            target_confidence=0.0,
            resolved_person_id="",
            identity_resolution="verified",
            message_value={},
            aggregation_info={},
            moderation_view={},
            intervention_candidate=False,
            intervention_reason="",
            metadata={
                "origin_system": "uchat",
                "platform": "console",
                "platform_user_id": "user_local_default",
                "console_user_id": "user_local_default",
                "console_display_name": "控制台用户",
                "username": "控制台用户",
            },
        )

    @classmethod
    def from_local_asr(
        cls,
        *,
        text: str,
        scene_id: str,
        session_window_id: str,
        speaker_id: str,
        speaker_display_name: str = "",
        voiceprint_evidence: list[dict[str, Any]] | None = None,
    ) -> "NormalizedEvent":
        trace_id = new_trace_id()
        stripped = text.strip()
        evidence = list(voiceprint_evidence or [])
        return cls(
            event_id=new_event_id(),
            trace_id=trace_id,
            occurred_at=utc_now_iso(),
            event_kind="speech",
            source_type="local_asr",
            scene_id=scene_id,
            session_window_id=session_window_id,
            content_raw=text,
            content_norm=stripped,
            speaker_candidates=[
                {
                    "entity_id": f"local_asr:{speaker_id}",
                    "speaker_role": "user",
                    "confidence": 0.9,
                    "source": "asr_speaker_profile",
                }
            ],
            binding_evidence=evidence,
            binding_confidence=max((float(item.get("confidence", 0.0) or 0.0) for item in evidence), default=0.0),
            target_kind="unknown",
            target_entity_id="",
            target_confidence=0.0,
            resolved_person_id="",
            identity_resolution="unknown",
            message_value={},
            aggregation_info={},
            moderation_view={},
            intervention_candidate=False,
            intervention_reason="",
            metadata={
                "origin_system": "uchat",
                "platform": "local_asr",
                "platform_user_id": speaker_id,
                "speaker_id": speaker_id,
                "speaker_display_name": speaker_display_name or speaker_id,
                "username": speaker_display_name or speaker_id,
            },
        )

    @classmethod
    def from_bilibili_danmaku(
        cls,
        *,
        text: str,
        scene_id: str,
        session_window_id: str,
        username: str,
        user_id: str,
        is_paid: bool = False,
        is_known_viewer: bool = False,
        room_id: str = "",
    ) -> "NormalizedEvent":
        trace_id = new_trace_id()
        stripped = text.strip()
        return cls(
            event_id=new_event_id(),
            trace_id=trace_id,
            occurred_at=utc_now_iso(),
            event_kind="speech",
            source_type="bilibili_danmaku",
            scene_id=scene_id,
            session_window_id=session_window_id,
            content_raw=text,
            content_norm=stripped,
            speaker_candidates=[
                {
                    "entity_id": f"bilibili:{user_id}",
                    "speaker_role": "viewer",
                    "confidence": 0.95,
                    "source": "platform_profile",
                }
            ],
            binding_evidence=[
                {
                    "evidence_type": "platform_user_id",
                    "value": user_id,
                    "confidence": 0.95,
                }
            ],
            binding_confidence=0.95,
            target_kind="streamer",
            target_entity_id="self_main",
            target_confidence=0.85,
            resolved_person_id="",
            identity_resolution="unknown",
            message_value={},
            aggregation_info={
                "is_aggregated_event": False,
                "aggregation_kind": "",
                "aggregation_window_ms": 0,
                "member_count": 1,
                "sample_messages": [],
            },
            moderation_view={
                "moderation_labels": [],
                "semantic_summary": stripped,
                "reply_policy": "normal_reply",
                "quote_allowed": True,
                "raw_content_ref": f"raw_{uuid4().hex[:8]}",
            },
            intervention_candidate=False,
            intervention_reason="",
            metadata={
                "origin_system": "uchat",
                "platform": "bilibili",
                "username": username,
                "platform_user_id": user_id,
                "room_id": room_id,
                "is_paid": is_paid,
                "is_known_viewer": is_known_viewer,
            },
        )

    @classmethod
    def from_system_event(
        cls,
        *,
        source_type: str,
        scene_id: str,
        session_window_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        event_kind: EventKind = "system",
    ) -> "NormalizedEvent":
        trace_id = new_trace_id()
        stripped = content.strip()
        return cls(
            event_id=new_event_id(),
            trace_id=trace_id,
            occurred_at=utc_now_iso(),
            event_kind=event_kind,
            source_type=source_type,
            scene_id=scene_id,
            session_window_id=session_window_id,
            content_raw=content,
            content_norm=stripped or content,
            target_kind="unknown",
            target_entity_id="",
            target_confidence=0.0,
            resolved_person_id="",
            identity_resolution="unknown",
            message_value={},
            aggregation_info={},
            moderation_view={},
            intervention_candidate=False,
            intervention_reason="",
            metadata={
                "origin_system": "uchat",
                **(metadata or {}),
            },
        )

    @classmethod
    def from_gateway_payload(
        cls,
        *,
        payload: GatewayInputEvent,
        scene_id: str,
        session_window_id: str,
    ) -> "NormalizedEvent":
        metadata = dict(payload.metadata)
        metadata.setdefault("origin_system", "bilibili_gateway")
        metadata.setdefault("platform", "bilibili")
        return cls(
            event_id=payload.event_id,
            trace_id=payload.trace_id,
            occurred_at=payload.occurred_at,
            event_kind=payload.event_kind,  # type: ignore[arg-type]
            source_type=payload.source_type,
            scene_id=scene_id,
            session_window_id=session_window_id,
            content_raw=payload.content_raw,
            content_norm=payload.content_norm,
            speaker_candidates=list(payload.speaker_candidates),
            binding_evidence=list(payload.binding_evidence),
            binding_confidence=payload.binding_confidence,
            target_kind=payload.target_kind,
            target_entity_id=payload.target_entity_id,
            target_confidence=payload.target_confidence,
            resolved_person_id=payload.resolved_person_id,
            identity_resolution=payload.identity_resolution,
            message_value=payload.message_value.to_dict(),
            aggregation_info=payload.aggregation_info.to_dict(),
            moderation_view=payload.moderation_view.to_dict(),
            intervention_candidate=payload.intervention_candidate,
            intervention_reason=payload.intervention_reason,
            emotion_context={},
            tool_context={},
            privacy_level=payload.privacy_level,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlannerDecision:
    decision_id: str
    trace_id: str
    action: DecisionAction
    reply: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    embodiment: dict[str, Any] = field(default_factory=dict)
    memory_intent: dict[str, Any] = field(default_factory=dict)
    reasoning_summary: str = ""

    @classmethod
    def skip(cls, trace_id: str, reason: str) -> "PlannerDecision":
        return cls(decision_id=new_decision_id(), trace_id=trace_id, action="skip", reasoning_summary=reason)

    @classmethod
    def quit(cls, trace_id: str) -> "PlannerDecision":
        return cls(decision_id=new_decision_id(), trace_id=trace_id, action="quit", reasoning_summary="console quit command")

    @classmethod
    def reply_text(cls, trace_id: str, text: str) -> "PlannerDecision":
        return cls(
            decision_id=new_decision_id(),
            trace_id=trace_id,
            action="reply",
            reply={"text": text, "style": "natural"},
            memory_intent={"should_record_reply": True},
            reasoning_summary="reply generated by configured model route",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutboundEvent:
    outbound_id: str
    trace_id: str
    channel: OutputChannel
    destination: OutputDestination
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def console_text(cls, *, trace_id: str, text: str) -> "OutboundEvent":
        return cls(
            outbound_id=new_outbound_id(),
            trace_id=trace_id,
            channel="text",
            destination="console",
            text=text,
            metadata={"origin_system": "uchat"},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextPackView:
    trace_id: str
    text: str
    source: str = "ltmem"
    fallback_source: str | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutputSegment:
    segment_id: str
    trace_id: str
    index: int
    text: str
    kind: OutputSegmentKind = "sentence"
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def sentence(cls, *, trace_id: str, index: int, text: str, metadata: dict[str, Any] | None = None) -> "OutputSegment":
        return cls(
            segment_id=new_segment_id(),
            trace_id=trace_id,
            index=index,
            text=text,
            kind="sentence",
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutputTask:
    task_id: str
    trace_id: str
    channel: OutputChannel
    destination: OutputDestination
    text: str
    priority: int = 50
    status: OutputTaskStatus = "pending"
    timeout_ms: int = 3000
    replace_key: str | None = None
    segment_index: int | None = None
    created_at: str = field(default_factory=utc_now_iso)
    queued_at_ms: float | None = None
    started_at_ms: float | None = None
    completed_at_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_segment(
        cls,
        *,
        segment: OutputSegment,
        destination: OutputDestination,
        channel: OutputChannel = "tts",
        priority: int = 80,
        timeout_ms: int = 3000,
        replace_key: str | None = None,
    ) -> "OutputTask":
        return cls(
            task_id=new_task_id(),
            trace_id=segment.trace_id,
            channel=channel,
            destination=destination,
            text=segment.text,
            priority=priority,
            timeout_ms=timeout_ms,
            replace_key=replace_key,
            segment_index=segment.index,
            metadata={"segment": segment.to_dict()},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliveryReceipt:
    receipt_id: str
    trace_id: str
    task_id: str
    channel: OutputChannel
    destination: OutputDestination
    status: DeliveryStatus
    delivered_at: str
    latency_ms: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def delivered(
        cls,
        *,
        trace_id: str,
        task_id: str,
        channel: OutputChannel,
        destination: OutputDestination,
        latency_ms: float | None = None,
        detail: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DeliveryReceipt":
        return cls(
            receipt_id=new_receipt_id(),
            trace_id=trace_id,
            task_id=task_id,
            channel=channel,
            destination=destination,
            status="delivered",
            delivered_at=utc_now_iso(),
            latency_ms=latency_ms,
            detail=detail or {},
            metadata=metadata or {},
        )

    @classmethod
    def failed(
        cls,
        *,
        trace_id: str,
        task_id: str,
        channel: OutputChannel,
        destination: OutputDestination,
        detail: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DeliveryReceipt":
        return cls(
            receipt_id=new_receipt_id(),
            trace_id=trace_id,
            task_id=task_id,
            channel=channel,
            destination=destination,
            status="failed",
            delivered_at=utc_now_iso(),
            detail=detail or {},
            metadata=metadata or {},
        )

    @classmethod
    def cancelled(
        cls,
        *,
        trace_id: str,
        task_id: str,
        channel: OutputChannel,
        destination: OutputDestination,
        detail: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DeliveryReceipt":
        return cls(
            receipt_id=new_receipt_id(),
            trace_id=trace_id,
            task_id=task_id,
            channel=channel,
            destination=destination,
            status="cancelled",
            delivered_at=utc_now_iso(),
            detail=detail or {},
            metadata=metadata or {},
        )

    @classmethod
    def skipped(
        cls,
        *,
        trace_id: str,
        task_id: str,
        channel: OutputChannel,
        destination: OutputDestination,
        detail: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DeliveryReceipt":
        return cls(
            receipt_id=new_receipt_id(),
            trace_id=trace_id,
            task_id=task_id,
            channel=channel,
            destination=destination,
            status="skipped",
            delivered_at=utc_now_iso(),
            detail=detail or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EmbodimentCommand:
    command_id: str
    trace_id: str
    body_id: str
    expression: str | None = None
    motion: str | None = None
    intensity: float = 0.0
    duration_ms: int = 0
    sync_to_audio: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def expression_command(
        cls,
        *,
        trace_id: str,
        body_id: str,
        expression: str,
        motion: str | None = None,
        intensity: float = 0.5,
        duration_ms: int = 1200,
        sync_to_audio: bool = True,
    ) -> "EmbodimentCommand":
        return cls(
            command_id=new_command_id(),
            trace_id=trace_id,
            body_id=body_id,
            expression=expression,
            motion=motion,
            intensity=intensity,
            duration_ms=duration_ms,
            sync_to_audio=sync_to_audio,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolInvocation:
    tool_name: str
    call_id: str
    arguments: dict[str, Any]
    session_id: str
    trace_id: str
    risk_level: str = "L0"
    idempotency_key: str | None = None

    @classmethod
    def create(
        cls,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        trace_id: str,
        risk_level: str = "L0",
    ) -> "ToolInvocation":
        call_id = new_tool_call_id()
        return cls(
            tool_name=tool_name,
            call_id=call_id,
            arguments=arguments,
            session_id=session_id,
            trace_id=trace_id,
            risk_level=risk_level,
            idempotency_key=call_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResult:
    tool_name: str
    call_id: str
    success: bool
    content: str = ""
    structured_content: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionState:
    session_id: str
    scene_id: str
    scene_state: SceneState
    session_snapshot: SessionStateSnapshot = field(default_factory=SessionStateSnapshot)
    current_state: str = "idle"
    degraded: bool = False
    degraded_reason: str = ""
    short_history: list[dict[str, str]] = field(default_factory=list)
    active_output_count: int = 0
    last_reply_at: str | None = None
    last_input_at: str | None = None
    reply_latency_stats: list[float] = field(default_factory=list)

    def remember_user(self, text: str) -> None:
        self.short_history.append({"role": "user", "content": text})
        self.last_input_at = utc_now_iso()
        self._trim()

    def remember_assistant(self, text: str) -> None:
        self.short_history.append({"role": "assistant", "content": text})
        self.last_reply_at = utc_now_iso()
        self._trim()

    def remember_reply_latency(self, latency_ms: float) -> None:
        self.reply_latency_stats.append(latency_ms)
        if len(self.reply_latency_stats) > 20:
            self.reply_latency_stats = self.reply_latency_stats[-20:]

    def render_short_history(self) -> str:
        if not self.short_history:
            return "（暂无）"
        return "\n".join(f"{item['role']}: {item['content']}" for item in self.short_history[-12:])

    def render_short_history_self_view(self) -> str:
        if not self.short_history:
            return "（暂无）"
        rendered: list[str] = []
        for item in self.short_history[-12:]:
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if role == "assistant":
                rendered.append(f"你刚才说：{content}")
            elif role == "user":
                rendered.append(f"对方刚才说：{content}")
            else:
                rendered.append(f"{role or 'unknown'}: {content}")
        return "\n".join(rendered) if rendered else "（暂无）"

    def _trim(self) -> None:
        if len(self.short_history) > 20:
            self.short_history = self.short_history[-20:]
