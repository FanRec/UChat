from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from uchat.contracts import NormalizedEvent


AttackIntensity = Literal["none", "low", "medium", "high"]
TargetScope = Literal["streamer", "viewer", "group", "unknown"]


@dataclass(frozen=True)
class ModerationResult:
    raw_input_text: str
    semantic_summary: str
    moderation_labels: list[str]
    reply_policy: str
    quote_allowed: bool
    attack_intensity: AttackIntensity
    target_scope: TargetScope
    raw_content_ref: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ModerationPipeline:
    def evaluate(self, event: NormalizedEvent) -> ModerationResult:
        moderation_view = dict(event.moderation_view or {})
        raw_text = event.content_raw
        semantic_summary = str(moderation_view.get("semantic_summary", "")).strip() or event.content_norm
        labels = [str(label) for label in moderation_view.get("moderation_labels", []) if str(label).strip()]
        reply_policy = str(moderation_view.get("reply_policy", "normal_reply")).strip() or "normal_reply"
        quote_allowed = bool(moderation_view.get("quote_allowed", True))
        raw_content_ref = str(moderation_view.get("raw_content_ref", "")).strip()
        target_scope = self._target_scope(event.target_kind)
        attack_intensity = self._attack_intensity(labels=labels, reply_policy=reply_policy)

        if not quote_allowed:
            semantic_summary = self._sanitize_summary(semantic_summary)

        return ModerationResult(
            raw_input_text=raw_text,
            semantic_summary=semantic_summary,
            moderation_labels=labels,
            reply_policy=reply_policy,
            quote_allowed=quote_allowed,
            attack_intensity=attack_intensity,
            target_scope=target_scope,
            raw_content_ref=raw_content_ref,
        )

    def reply_text(self, result: ModerationResult, fallback_text: str) -> str:
        text = result.semantic_summary.strip() or fallback_text.strip()
        return text or fallback_text

    def _attack_intensity(self, *, labels: list[str], reply_policy: str) -> AttackIntensity:
        if not labels:
            return "none"
        if reply_policy in {"drop", "skip"}:
            return "high"
        if reply_policy == "observe_only":
            return "medium"
        if len(labels) >= 2:
            return "medium"
        return "low"

    def _target_scope(self, target_kind: str) -> TargetScope:
        normalized = str(target_kind).strip()
        if normalized in {"streamer", "viewer", "group"}:
            return normalized  # type: ignore[return-value]
        return "unknown"

    def _sanitize_summary(self, summary: str) -> str:
        if not summary:
            return summary
        return summary.replace("“", "").replace("”", "").replace("\"", "").strip()
