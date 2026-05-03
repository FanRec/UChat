from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_trace_id() -> str:
    return f"trace_{uuid4().hex[:16]}"


def new_event_id() -> str:
    return f"gw_{uuid4().hex[:16]}"


def new_raw_content_ref() -> str:
    return f"raw_{uuid4().hex[:8]}"


def ms_to_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def build_event(
    *,
    event_type: str,
    source_type: str,
    event_kind: str,
    content_raw: str,
    content_norm: str,
    room_id: str,
    username: str,
    user_id: str,
    occurred_at: str,
    target: dict[str, Any],
    message_value: dict[str, Any],
    moderation: dict[str, Any],
    aggregation_info: dict[str, Any],
    metadata: dict[str, Any],
    intervention_candidate: bool,
    intervention_reason: str,
) -> dict[str, Any]:
    return {
        "event_id": new_event_id(),
        "trace_id": new_trace_id(),
        "source_type": source_type,
        "event_kind": event_kind,
        "occurred_at": occurred_at,
        "content_raw": content_raw,
        "content_norm": content_norm,
        "speaker_candidates": [
            {
                "entity_id": f"bilibili:{user_id}" if user_id else "",
                "speaker_role": "viewer" if user_id else "system",
                "confidence": 0.95 if user_id else 1.0,
                "source": "platform_profile" if user_id else "gateway_system",
            }
        ],
        "binding_evidence": [
            {
                "evidence_type": "platform_user_id",
                "value": user_id,
                "confidence": 0.95,
            }
        ] if user_id else [],
        "binding_confidence": 0.95 if user_id else 1.0,
        "resolved_person_id": "",
        "identity_resolution": "unknown",
        "target_kind": target["target_kind"],
        "target_entity_id": target["target_entity_id"],
        "target_confidence": target["target_confidence"],
        "message_value": message_value,
        "aggregation_info": aggregation_info,
        "moderation_view": {
            "moderation_labels": moderation["moderation_labels"],
            "semantic_summary": moderation["semantic_summary"],
            "reply_policy": moderation["reply_policy"],
            "quote_allowed": moderation["quote_allowed"],
            "raw_content_ref": moderation["raw_content_ref"],
        },
        "intervention_candidate": intervention_candidate,
        "intervention_reason": intervention_reason,
        "privacy_level": 1,
        "metadata": metadata | {"platform": "bilibili", "username": username, "platform_user_id": user_id, "room_id": room_id},
    }


def aggregated_danmaku_payload(
    *,
    room_id: str,
    occurred_at: str,
    content_raw: str,
    content_norm: str,
    moderation: dict[str, Any],
    aggregation_info: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return build_event(
        event_type="danmaku",
        source_type="bilibili_danmaku",
        event_kind="speech",
        content_raw=content_raw,
        content_norm=content_norm,
        room_id=room_id,
        username="",
        user_id="",
        occurred_at=occurred_at,
        target={"target_kind": "unknown", "target_entity_id": "", "target_confidence": 0.2},
        message_value={"reply_value": 0.28, "show_value": 0.42, "memory_value": 0.25, "relationship_value": 0.1, "risk_score": 0.0},
        moderation=moderation,
        aggregation_info=aggregation_info,
        metadata=metadata,
        intervention_candidate=False,
        intervention_reason="",
    )
