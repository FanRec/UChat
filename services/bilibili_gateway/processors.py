from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GatewayMetrics:
    raw_inbound_count: int = 0
    normalized_count: int = 0
    aggregated_count: int = 0
    reply_candidate_count: int = 0
    risk_event_count: int = 0
    gift_combo_open_count: int = 0
    milestone_emit_count: int = 0
    combo_summary_emit_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "raw_inbound_count": self.raw_inbound_count,
            "normalized_count": self.normalized_count,
            "aggregated_count": self.aggregated_count,
            "reply_candidate_count": self.reply_candidate_count,
            "risk_event_count": self.risk_event_count,
            "gift_combo_open_count": self.gift_combo_open_count,
            "milestone_emit_count": self.milestone_emit_count,
            "combo_summary_emit_count": self.combo_summary_emit_count,
        }


class RiskTagger:
    def __init__(self, *, rule_file: str, default_reply_policy: str) -> None:
        self.default_reply_policy = default_reply_policy
        self._rules = self._load_rules(rule_file)

    def tag(self, text: str) -> dict[str, Any]:
        labels = [label for needle, label in self._rules if needle and needle in text.lower()]
        risk_score = min(1.0, 0.32 * len(labels))
        reply_policy = "observe_only" if labels else self.default_reply_policy
        return {
            "moderation_labels": labels,
            "semantic_summary": text.strip()[:120],
            "reply_policy": reply_policy,
            "quote_allowed": not labels,
            "raw_content_ref": "",
            "risk_score": risk_score,
        }

    @staticmethod
    def _load_rules(path: str) -> list[tuple[str, str]]:
        if not path:
            return [
                ("傻", "abuse"),
                ("滚", "abuse"),
                ("死", "self_harm_or_violence"),
                ("举报", "brigading"),
            ]
        rule_path = Path(path)
        if not rule_path.exists():
            return []
        rules: list[tuple[str, str]] = []
        for raw_line in rule_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            needle, _, label = line.partition("=")
            rules.append((needle.strip().lower(), (label or "risk").strip()))
        return rules


class SceneStatsTracker:
    def __init__(self, *, window_ms: int) -> None:
        self.window_ms = window_ms
        self._records: deque[tuple[int, str]] = deque()
        self._audience_signals: deque[tuple[int, int]] = deque()
        self._risk_signals: deque[tuple[int, float]] = deque()
        self._engagement_signals: deque[tuple[int, float]] = deque()

    def observe(
        self,
        *,
        timestamp_ms: int,
        kind: str,
        audience_signal: int = 0,
        risk_score: float = 0.0,
        engagement_signal: float = 0.0,
    ) -> dict[str, str]:
        self._records.append((timestamp_ms, kind))
        if audience_signal > 0:
            self._audience_signals.append((timestamp_ms, audience_signal))
        if risk_score > 0:
            self._risk_signals.append((timestamp_ms, risk_score))
        if engagement_signal > 0:
            self._engagement_signals.append((timestamp_ms, engagement_signal))
        self._trim(timestamp_ms)
        return self.snapshot(timestamp_ms)

    def snapshot(self, timestamp_ms: int) -> dict[str, str]:
        self._trim(timestamp_ms)
        danmaku_count = sum(1 for _, item_kind in self._records if item_kind == "danmaku")
        audience_value = sum(value for _, value in self._audience_signals)
        risk_total = sum(value for _, value in self._risk_signals)
        engagement_total = sum(value for _, value in self._engagement_signals)
        return {
            "danmaku_velocity": _bucketize(danmaku_count, low=3, high=8),
            "audience_density": _bucketize(audience_value, low=1000, high=5000),
            "risk_level": _risk_level(risk_total),
            "engagement_level": _engagement_level(engagement_total),
        }

    def _trim(self, timestamp_ms: int) -> None:
        threshold = timestamp_ms - self.window_ms
        while self._records and self._records[0][0] < threshold:
            self._records.popleft()
        while self._audience_signals and self._audience_signals[0][0] < threshold:
            self._audience_signals.popleft()
        while self._risk_signals and self._risk_signals[0][0] < threshold:
            self._risk_signals.popleft()
        while self._engagement_signals and self._engagement_signals[0][0] < threshold:
            self._engagement_signals.popleft()


class DedupeTracker:
    def __init__(self, *, window_ms: int) -> None:
        self.window_ms = window_ms
        self._last_seen: dict[str, int] = {}

    def should_fold(self, text: str, timestamp_ms: int) -> bool:
        key = text.strip().lower()
        if not key:
            return True
        previous = self._last_seen.get(key)
        self._last_seen[key] = timestamp_ms
        if previous is None:
            return False
        return timestamp_ms - previous <= self.window_ms


class DanmakuAggregator:
    def __init__(self, *, window_ms: int) -> None:
        self.window_ms = window_ms
        self._samples: list[str] = []
        self._first_at_ms: int | None = None
        self._last_room_id = ""

    def ingest(self, *, room_id: str, text: str, timestamp_ms: int) -> None:
        if self._first_at_ms is None:
            self._first_at_ms = timestamp_ms
        self._last_room_id = room_id
        if text.strip():
            self._samples.append(text.strip())

    def flush(self, *, timestamp_ms: int) -> dict[str, Any] | None:
        if self._first_at_ms is None or not self._samples:
            return None
        if timestamp_ms - self._first_at_ms < self.window_ms:
            return None
        sample_messages = self._samples[:5]
        payload = {
            "content_raw": " / ".join(sample_messages),
            "content_norm": "；".join(sample_messages),
            "aggregation_info": {
                "is_aggregated_event": True,
                "aggregation_kind": "danmaku_window",
                "aggregation_window_ms": self.window_ms,
                "member_count": len(self._samples),
                "sample_messages": sample_messages,
            },
            "room_id": self._last_room_id,
        }
        self._samples = []
        self._first_at_ms = None
        self._last_room_id = ""
        return payload


@dataclass
class GiftComboState:
    room_id: str
    platform_user_id: str
    username: str
    gift_id: str
    gift_name: str
    combo_session_id: str
    start_ms: int
    last_seen_ms: int
    count: int = 0
    total_value: int = 0
    combo_state: str = "combo_started"
    emitted_milestones: set[int] = field(default_factory=set)
    last_update_emit_ms: int = 0
    reply_candidates_emitted: int = 0

    def key(self) -> str:
        return f"{self.room_id}:{self.platform_user_id}:{self.gift_id}:{self.combo_session_id}"


class GiftComboTracker:
    def __init__(self, *, config: Any, metrics: GatewayMetrics) -> None:
        self.config = config
        self.metrics = metrics
        self._states: dict[str, GiftComboState] = {}

    def ingest(self, gift: dict[str, Any], timestamp_ms: int) -> list[GiftComboState]:
        combo_session_id = str(gift.get("combo_session_id") or gift.get("tid") or gift.get("rnd") or "derived")
        state = self._states.get(f"{gift['room_id']}:{gift['platform_user_id']}:{gift['gift_id']}:{combo_session_id}")
        if state is None:
            state = GiftComboState(
                room_id=str(gift["room_id"]),
                platform_user_id=str(gift["platform_user_id"]),
                username=str(gift["username"]),
                gift_id=str(gift["gift_id"]),
                gift_name=str(gift["gift_name"]),
                combo_session_id=combo_session_id,
                start_ms=timestamp_ms,
                last_seen_ms=timestamp_ms,
            )
            self._states[state.key()] = state
            self.metrics.gift_combo_open_count += 1
        else:
            state.combo_state = "combo_active"
            state.last_seen_ms = timestamp_ms
        state.count += int(gift.get("count", 1))
        state.total_value += int(gift.get("total_value", 0))
        return [state]

    def flush_expired(self, timestamp_ms: int) -> list[GiftComboState]:
        expired: list[GiftComboState] = []
        expired_keys: list[str] = []
        for key, state in self._states.items():
            if timestamp_ms - state.last_seen_ms < self.config.combo_quiet_timeout_ms:
                if state.combo_state == "combo_active":
                    state.combo_state = "combo_settling"
                continue
            state.combo_state = "combo_closed"
            expired.append(state)
            expired_keys.append(key)
        for key in expired_keys:
            self._states.pop(key, None)
        return expired

    def next_milestone(self, count: int) -> int | None:
        milestones = [value for value in self.config.milestone_counts if value > 1 and count >= value]
        if not milestones:
            return None
        return max(milestones)

    @staticmethod
    def compute_show_value(total_value: int) -> float:
        return min(1.0, 0.2 + math.log10(max(total_value, 1) + 1) / 5)


def classify_target(text: str) -> dict[str, Any]:
    if "主播" in text or "@主播" in text or "唱歌吗" in text or "你" in text:
        return {"target_kind": "streamer", "target_entity_id": "self_main", "target_confidence": 0.9}
    if "@" in text:
        return {"target_kind": "viewer", "target_entity_id": "", "target_confidence": 0.75}
    return {"target_kind": "unknown", "target_entity_id": "", "target_confidence": 0.35}


def score_danmaku(*, text: str, target_kind: str, risk_score: float) -> dict[str, float]:
    is_question = any(marker in text for marker in ("?", "？", "吗", "么", "什么", "为什么"))
    reply_value = 0.15
    if target_kind == "streamer":
        reply_value += 0.25
    if is_question:
        reply_value += 0.35
    if len(text) >= 8:
        reply_value += 0.1
    reply_value = max(0.0, min(1.0, reply_value - risk_score * 0.4))
    show_value = min(1.0, reply_value + 0.08 + (0.06 if is_question else 0.0))
    return {
        "reply_value": round(reply_value, 2),
        "show_value": round(show_value, 2),
        "memory_value": 0.22,
        "relationship_value": 0.18 if target_kind == "streamer" else 0.08,
        "risk_score": round(risk_score, 2),
    }


def _bucketize(value: int, *, low: int, high: int) -> str:
    if value >= high:
        return "high"
    if value >= low:
        return "normal"
    if value > 0:
        return "low"
    return "n/a"


def _risk_level(risk_total: float) -> str:
    if risk_total >= 1.2:
        return "L3"
    if risk_total >= 0.7:
        return "L2"
    if risk_total > 0:
        return "L1"
    return "L0"


def _engagement_level(engagement_total: float) -> str:
    if engagement_total >= 4.5:
        return "high"
    if engagement_total >= 1.8:
        return "normal"
    if engagement_total > 0:
        return "low"
    return "n/a"
