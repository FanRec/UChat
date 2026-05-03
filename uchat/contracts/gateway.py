from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class GatewayMessageValue:
    reply_value: float
    show_value: float
    memory_value: float
    relationship_value: float
    risk_score: float

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GatewayMessageValue":
        required = ("reply_value", "show_value", "memory_value", "relationship_value", "risk_score")
        _require_keys(payload, required, "message_value")
        return cls(
            reply_value=float(payload["reply_value"]),
            show_value=float(payload["show_value"]),
            memory_value=float(payload["memory_value"]),
            relationship_value=float(payload["relationship_value"]),
            risk_score=float(payload["risk_score"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GatewayAggregationInfo:
    is_aggregated_event: bool
    aggregation_kind: str
    aggregation_window_ms: int
    member_count: int
    sample_messages: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GatewayAggregationInfo":
        required = ("is_aggregated_event", "aggregation_kind", "aggregation_window_ms", "member_count", "sample_messages")
        _require_keys(payload, required, "aggregation_info")
        sample_messages = payload["sample_messages"]
        if not isinstance(sample_messages, list):
            raise ValueError("aggregation_info.sample_messages must be a list")
        return cls(
            is_aggregated_event=bool(payload["is_aggregated_event"]),
            aggregation_kind=str(payload["aggregation_kind"]),
            aggregation_window_ms=int(payload["aggregation_window_ms"]),
            member_count=int(payload["member_count"]),
            sample_messages=[str(item) for item in sample_messages],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GatewayModerationView:
    moderation_labels: list[str]
    semantic_summary: str
    reply_policy: str
    quote_allowed: bool
    raw_content_ref: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GatewayModerationView":
        required = ("moderation_labels", "semantic_summary", "reply_policy", "quote_allowed", "raw_content_ref")
        _require_keys(payload, required, "moderation_view")
        labels = payload["moderation_labels"]
        if not isinstance(labels, list):
            raise ValueError("moderation_view.moderation_labels must be a list")
        return cls(
            moderation_labels=[str(item) for item in labels],
            semantic_summary=str(payload["semantic_summary"]),
            reply_policy=str(payload["reply_policy"]),
            quote_allowed=bool(payload["quote_allowed"]),
            raw_content_ref=str(payload["raw_content_ref"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GatewayInputEvent:
    event_id: str
    trace_id: str
    source_type: str
    event_kind: str
    occurred_at: str
    content_raw: str
    content_norm: str
    speaker_candidates: list[dict[str, Any]]
    binding_evidence: list[dict[str, Any]]
    binding_confidence: float
    resolved_person_id: str
    identity_resolution: str
    target_kind: str
    target_entity_id: str
    target_confidence: float
    message_value: GatewayMessageValue
    aggregation_info: GatewayAggregationInfo
    moderation_view: GatewayModerationView
    intervention_candidate: bool
    intervention_reason: str
    privacy_level: int
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GatewayInputEvent":
        required = (
            "event_id",
            "trace_id",
            "source_type",
            "event_kind",
            "occurred_at",
            "content_raw",
            "content_norm",
            "speaker_candidates",
            "binding_evidence",
            "binding_confidence",
            "resolved_person_id",
            "identity_resolution",
            "target_kind",
            "target_entity_id",
            "target_confidence",
            "message_value",
            "aggregation_info",
            "moderation_view",
            "intervention_candidate",
            "intervention_reason",
            "privacy_level",
            "metadata",
        )
        _require_keys(payload, required, "gateway_event")
        _validate_event_required_fields(payload)
        speaker_candidates = payload["speaker_candidates"]
        binding_evidence = payload["binding_evidence"]
        metadata = payload["metadata"]
        if not isinstance(speaker_candidates, list):
            raise ValueError("speaker_candidates must be a list")
        if not isinstance(binding_evidence, list):
            raise ValueError("binding_evidence must be a list")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        return cls(
            event_id=str(payload["event_id"]),
            trace_id=str(payload["trace_id"]),
            source_type=str(payload["source_type"]),
            event_kind=str(payload["event_kind"]),
            occurred_at=str(payload["occurred_at"]),
            content_raw=str(payload["content_raw"]),
            content_norm=str(payload["content_norm"]),
            speaker_candidates=[dict(item) for item in speaker_candidates],
            binding_evidence=[dict(item) for item in binding_evidence],
            binding_confidence=float(payload["binding_confidence"]),
            resolved_person_id=str(payload["resolved_person_id"]),
            identity_resolution=str(payload["identity_resolution"]),
            target_kind=str(payload["target_kind"]),
            target_entity_id=str(payload["target_entity_id"]),
            target_confidence=float(payload["target_confidence"]),
            message_value=GatewayMessageValue.from_dict(dict(payload["message_value"])),
            aggregation_info=GatewayAggregationInfo.from_dict(dict(payload["aggregation_info"])),
            moderation_view=GatewayModerationView.from_dict(dict(payload["moderation_view"])),
            intervention_candidate=bool(payload["intervention_candidate"]),
            intervention_reason=str(payload["intervention_reason"]),
            privacy_level=int(payload["privacy_level"]),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["message_value"] = self.message_value.to_dict()
        payload["aggregation_info"] = self.aggregation_info.to_dict()
        payload["moderation_view"] = self.moderation_view.to_dict()
        return payload


def _require_keys(payload: dict[str, Any], keys: tuple[str, ...], context: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"{context} missing required keys: {', '.join(missing)}")


def _validate_event_required_fields(payload: dict[str, Any]) -> None:
    source_type = str(payload.get("source_type", ""))
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    if str(metadata.get("platform", "")) != "bilibili":
        raise ValueError("metadata.platform must be 'bilibili'")
    if "room_id" not in metadata:
        raise ValueError("metadata.room_id is required")
    if source_type == "bilibili_room_state" and "room_state" not in metadata:
        raise ValueError("metadata.room_state is required for bilibili_room_state")
    if source_type in {"bilibili_gift_combo_update", "bilibili_gift_combo_summary"}:
        for key in ("combo_count", "combo_total_value", "combo_state"):
            if key not in metadata:
                raise ValueError(f"metadata.{key} is required for {source_type}")
