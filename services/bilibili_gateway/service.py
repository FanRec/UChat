from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from services.bilibili_gateway.config import GatewayServiceConfig
from services.bilibili_gateway.connectors import (
    BLiveDMHandler,
    BLiveDMRoomConnector,
    OfflineDanmakuUnavailableError,
    OfflineHistoryRoomConnector,
    RoomConnector,
)
from services.bilibili_gateway.event_builder import (
    aggregated_danmaku_payload,
    build_event,
    ms_to_iso,
    new_event_id,
    new_raw_content_ref,
    new_trace_id,
    utc_now_iso,
)
from services.bilibili_gateway.processors import (
    DanmakuAggregator,
    DedupeTracker,
    GatewayMetrics,
    GiftComboState,
    GiftComboTracker,
    RiskTagger,
    SceneStatsTracker,
    classify_target,
    score_danmaku,
)
from services.bilibili_gateway.stores import EventStore


_CONSOLE = Console(stderr=True)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class RoomPipeline:
    room_id: str
    config: GatewayServiceConfig
    metrics: GatewayMetrics
    store: EventStore
    risk_tagger: RiskTagger
    stats_tracker: SceneStatsTracker
    dedupe: DedupeTracker
    danmaku_aggregator: DanmakuAggregator
    gift_combos: GiftComboTracker
    connector: RoomConnector | None = None
    connected: bool = False
    connected_at: str | None = None
    connection_mode: str = "mock"
    requested_connection_mode: str = "mock"
    live_diagnostics: dict[str, Any] | None = None

    def queue_stats(self) -> dict[str, Any]:
        return self.store.stats() | self.metrics.to_dict() | {
            "connected": self.connected,
            "connected_at": self.connected_at,
            "connection_mode": self.connection_mode,
            "requested_connection_mode": self.requested_connection_mode,
            "live_diagnostics": dict(self.live_diagnostics or {}),
        }


class BilibiliGatewayService:
    def __init__(self, config: GatewayServiceConfig) -> None:
        self.config = config
        self.logger = logging.getLogger("services.bilibili_gateway")
        self._rooms: dict[str, RoomPipeline] = {}
        self._flush_task: asyncio.Task[None] | None = None
        self._debug_enabled = config.observability.debug_dump_enabled
        self._debug_dump_dir = Path(config.observability.debug_dump_dir)

    def now_ms(self) -> int:
        return now_ms()

    async def start_background_tasks(self) -> None:
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop(), name="bilibili-gateway-flush")

    async def stop_background_tasks(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None
        for room_id in list(self._rooms):
            await self.disconnect(room_id)

    async def connect(self, room_id: str | None = None, connection_mode: str | None = None) -> dict[str, Any]:
        resolved_room_id = str(room_id or self.config.room.room_id)
        resolved_mode = self._resolve_connection_mode(connection_mode)
        room = self.ensure_room_pipeline(resolved_room_id)
        if room.connected:
            if room.connection_mode == resolved_mode:
                self.debug("connect", f"room={resolved_room_id} already connected mode={room.connection_mode}", style="cyan")
                return self._connect_response(room)
            await self.disconnect(resolved_room_id)
            room = self.ensure_room_pipeline(resolved_room_id, replace_existing=True)

        room.connected = True
        room.connected_at = utc_now_iso()
        room.connection_mode = resolved_mode
        room.requested_connection_mode = resolved_mode
        room.live_diagnostics = self._initial_live_diagnostics(room_id=resolved_room_id, requested_mode=resolved_mode)

        try:
            if resolved_mode == "live":
                await self._connect_live_room(room)
            elif resolved_mode == "offline_history":
                await self._connect_offline_history_room(room)
            else:
                room.connector = None
                self.record_live_stage(
                    room_id=resolved_room_id,
                    stage="connect_mode",
                    status="ok",
                    detail={"connection_mode": resolved_mode},
                )
        except Exception:
            room.connected = False
            room.connected_at = None
            room.connector = None
            raise

        self.debug(
            "connect",
            f"room={resolved_room_id} connected mock_mode={self.config.service.mock_mode} mode={room.connection_mode}",
            style="green",
        )
        return self._connect_response(room)

    async def disconnect(self, room_id: str | None = None) -> dict[str, Any]:
        resolved_room_id = str(room_id or self.config.room.room_id)
        room = self._rooms.get(resolved_room_id)
        if room is None:
            return {"status": "not_found", "room_id": resolved_room_id}
        if room.connector is not None:
            await room.connector.stop()
        room.store.clear()
        self._rooms.pop(resolved_room_id, None)
        self.debug("disconnect", f"room={resolved_room_id} disconnected", style="yellow")
        return {"status": "stopped", "room_id": resolved_room_id}

    def ensure_room_pipeline(self, room_id: str, *, replace_existing: bool = False) -> RoomPipeline:
        if replace_existing or room_id not in self._rooms:
            self._rooms[room_id] = self._create_room(room_id)
        return self._rooms[room_id]

    def get_connected_room(self, room_id: str | None = None) -> RoomPipeline:
        resolved_room_id = str(room_id or self.config.room.room_id)
        room = self._rooms.get(resolved_room_id)
        if room is None or not room.connected:
            raise RuntimeError(f"room {resolved_room_id} is not connected")
        return room

    def room(self, room_id: str | None = None) -> RoomPipeline:
        return self.ensure_room_pipeline(str(room_id or self.config.room.room_id))

    def poll_events(self, *, room_id: str | None, cursor: str, limit: int) -> dict[str, Any]:
        room = self.room(room_id)
        self.flush_room(room.room_id, now_ms())
        effective_limit = max(1, min(limit or self.config.queue.poll_default_limit, self.config.queue.max_events))
        events, next_cursor, cursor_expired = room.store.poll(cursor=cursor, limit=effective_limit)
        return {
            "room_id": room.room_id,
            "events": events,
            "next_cursor": next_cursor,
            "cursor_expired": cursor_expired,
            "queue_stats": room.queue_stats(),
        }

    def ingest_test_event(self, *, room_id: str | None, event_type: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        room = self.get_connected_room(room_id)
        if room.connection_mode == "live":
            raise RuntimeError(f"room {room.room_id} is in live mode; test event injection is disabled")
        normalized_type = event_type.strip().lower()
        if normalized_type == "danmaku":
            return self.ingest_danmaku(room_id=room.room_id, raw=raw)
        if normalized_type == "gift":
            return self.ingest_gift(room_id=room.room_id, raw=raw)
        if normalized_type == "sc":
            return self.ingest_super_chat(room_id=room.room_id, raw=raw)
        if normalized_type == "follow":
            return self.ingest_follow(room_id=room.room_id, raw=raw)
        if normalized_type == "room_state":
            return self.ingest_room_state(room_id=room.room_id, raw=raw)
        raise ValueError(f"unsupported test event type: {event_type}")

    def ingest_danmaku(self, *, room_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        room = self.room(room_id)
        room.metrics.raw_inbound_count += 1
        timestamp_ms = int(raw.get("timestamp") or now_ms())
        text = str(raw.get("msg", "")).strip()
        self._debug_raw("DANMAKU", room_id, raw, accent="bright_cyan")
        if room.dedupe.should_fold(text, timestamp_ms):
            self.debug("dedupe", f"room={room_id} folded repeated danmaku: {text}", style="yellow")
            room.danmaku_aggregator.ingest(room_id=room_id, text=text, timestamp_ms=timestamp_ms)
            return []

        target = classify_target(text)
        moderation = self._with_raw_content_ref(room.risk_tagger.tag(text))
        message_value = score_danmaku(
            text=text,
            target_kind=target["target_kind"],
            risk_score=float(moderation["risk_score"]),
        )
        scene_stats = room.stats_tracker.observe(
            timestamp_ms=timestamp_ms,
            kind="danmaku",
            audience_signal=100,
            risk_score=float(moderation["risk_score"]),
            engagement_signal=self._engagement_signal_for_danmaku(
                text=text,
                target_kind=target["target_kind"],
                reply_value=float(message_value["reply_value"]),
            ),
        )
        payload = build_event(
            event_type="danmaku",
            source_type="bilibili_danmaku",
            event_kind="speech",
            content_raw=text,
            content_norm=text,
            room_id=room_id,
            username=str(raw.get("uname", "")),
            user_id=str(raw.get("uid", "")),
            occurred_at=ms_to_iso(timestamp_ms),
            target=target,
            message_value=message_value,
            moderation=moderation,
            aggregation_info={
                "is_aggregated_event": False,
                "aggregation_kind": "",
                "aggregation_window_ms": 0,
                "member_count": 1,
                "sample_messages": [],
            },
            metadata={
                "platform": "bilibili",
                "room_id": room_id,
                "is_paid": False,
                "is_known_viewer": False,
                **scene_stats,
            },
            intervention_candidate=target["target_kind"] == "viewer",
            intervention_reason="viewer_to_viewer_chat" if target["target_kind"] == "viewer" else "",
        )
        events = [self._emit(room, payload)]
        aggregated = room.danmaku_aggregator.flush(timestamp_ms=timestamp_ms)
        if aggregated is not None:
            events.append(self._emit_aggregated_danmaku(room, aggregated=aggregated, timestamp_ms=timestamp_ms, scene_stats=scene_stats))
        return events

    def ingest_gift(self, *, room_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        room = self.room(room_id)
        room.metrics.raw_inbound_count += 1
        timestamp_ms = int(raw.get("timestamp") or now_ms())
        total_value = int(raw.get("total_coin") or 0)
        self._debug_raw("GIFT", room_id, raw, accent="bright_yellow")
        gift_payload = {
            "room_id": room_id,
            "platform_user_id": str(raw.get("uid", "")),
            "username": str(raw.get("uname", "")),
            "gift_id": str(raw.get("gift_id", "")),
            "gift_name": str(raw.get("gift_name", "礼物")),
            "count": int(raw.get("num", 1)),
            "total_value": total_value,
            "combo_session_id": str(raw.get("combo_session_id") or raw.get("tid") or raw.get("rnd") or ""),
        }
        scene_stats = room.stats_tracker.observe(
            timestamp_ms=timestamp_ms,
            kind="gift",
            audience_signal=max(total_value, 500),
            engagement_signal=1.6,
        )
        states = room.gift_combos.ingest(gift_payload, timestamp_ms)
        events: list[dict[str, Any]] = []
        for state in states:
            if state.count == gift_payload["count"]:
                state.reply_candidates_emitted = 1
                events.append(self._emit(room, self._gift_combo_event(state, timestamp_ms=timestamp_ms, allow_reply=True, source_type="bilibili_gift", event_type="gift", scene_stats=scene_stats)))
                continue
            milestone = room.gift_combos.next_milestone(state.count)
            if milestone is not None and milestone not in state.emitted_milestones and timestamp_ms - state.last_update_emit_ms >= self.config.combo.update_throttle_ms:
                state.emitted_milestones.add(milestone)
                state.last_update_emit_ms = timestamp_ms
                room.metrics.milestone_emit_count += 1
                events.append(self._emit(room, self._gift_combo_event(state, timestamp_ms=timestamp_ms, allow_reply=False, source_type="bilibili_gift_combo_update", event_type="gift_combo_update", scene_stats=scene_stats)))
        return events

    def ingest_super_chat(self, *, room_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        room = self.room(room_id)
        room.metrics.raw_inbound_count += 1
        timestamp_ms = now_ms()
        text = str(raw.get("message", "")).strip()
        price = int(raw.get("price", 0))
        self._debug_raw("SC", room_id, raw, accent="bright_red")
        moderation = self._with_raw_content_ref(room.risk_tagger.tag(text))
        scene_stats = room.stats_tracker.observe(
            timestamp_ms=timestamp_ms,
            kind="sc",
            audience_signal=max(price * 100, 1000),
            risk_score=float(moderation["risk_score"]),
            engagement_signal=2.5,
        )
        payload = build_event(
            event_type="sc",
            source_type="bilibili_sc",
            event_kind="feedback",
            content_raw=text,
            content_norm=text,
            room_id=room_id,
            username=str(raw.get("uname", "")),
            user_id=str(raw.get("uid", "")),
            occurred_at=ms_to_iso(timestamp_ms),
            target={"target_kind": "streamer", "target_entity_id": "self_main", "target_confidence": 0.98},
            message_value={
                "reply_value": 0.92,
                "show_value": 0.95,
                "memory_value": 0.55,
                "relationship_value": 0.7,
                "risk_score": float(moderation["risk_score"]),
            },
            moderation=moderation,
            aggregation_info={"is_aggregated_event": False, "aggregation_kind": "", "aggregation_window_ms": 0, "member_count": 1, "sample_messages": []},
            metadata={"platform": "bilibili", "room_id": room_id, "price": price, "is_paid": True, "is_known_viewer": True, **scene_stats},
            intervention_candidate=False,
            intervention_reason="",
        )
        return [self._emit(room, payload)]

    def ingest_follow(self, *, room_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        room = self.room(room_id)
        room.metrics.raw_inbound_count += 1
        timestamp_ms = now_ms()
        self._debug_raw("FOLLOW", room_id, raw, accent="bright_green")
        scene_stats = room.stats_tracker.observe(
            timestamp_ms=timestamp_ms,
            kind="follow",
            audience_signal=600,
            engagement_signal=1.2,
        )
        moderation = self._with_raw_content_ref(
            {
                "moderation_labels": [],
                "semantic_summary": "用户进行了关注或舰长支持",
                "reply_policy": "normal_reply",
                "quote_allowed": True,
                "risk_score": 0.0,
            }
        )
        payload = build_event(
            event_type="follow",
            source_type="bilibili_follow",
            event_kind="feedback",
            content_raw=f"{raw.get('uname', '')} 进行了关注/舰长支持",
            content_norm=f"{raw.get('uname', '')} 进行了关注/舰长支持",
            room_id=room_id,
            username=str(raw.get("uname", "")),
            user_id=str(raw.get("uid", "")),
            occurred_at=ms_to_iso(timestamp_ms),
            target={"target_kind": "streamer", "target_entity_id": "self_main", "target_confidence": 0.95},
            message_value={"reply_value": 0.64, "show_value": 0.72, "memory_value": 0.38, "relationship_value": 0.58, "risk_score": 0.0},
            moderation=moderation,
            aggregation_info={"is_aggregated_event": False, "aggregation_kind": "", "aggregation_window_ms": 0, "member_count": 1, "sample_messages": []},
            metadata={"platform": "bilibili", "room_id": room_id, **scene_stats},
            intervention_candidate=False,
            intervention_reason="",
        )
        return [self._emit(room, payload)]

    def ingest_room_state(self, *, room_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        room = self.room(room_id)
        room.metrics.raw_inbound_count += 1
        timestamp_ms = now_ms()
        popularity = int(raw.get("popularity", 0))
        self._debug_raw("ROOM_STATE", room_id, raw, accent="bright_blue")
        scene_stats = room.stats_tracker.observe(timestamp_ms=timestamp_ms, kind="room_state", audience_signal=popularity)
        moderation = self._with_raw_content_ref(
            {
                "moderation_labels": [],
                "semantic_summary": str(raw.get("state", "room_state")),
                "reply_policy": "observe_only",
                "quote_allowed": True,
                "risk_score": 0.0,
            }
        )
        payload = build_event(
            event_type="room_state",
            source_type="bilibili_room_state",
            event_kind="system",
            content_raw=str(raw.get("state", "room_state")),
            content_norm=str(raw.get("state", "room_state")),
            room_id=room_id,
            username="",
            user_id="",
            occurred_at=ms_to_iso(timestamp_ms),
            target={"target_kind": "unknown", "target_entity_id": "", "target_confidence": 0.0},
            message_value={"reply_value": 0.0, "show_value": 0.18, "memory_value": 0.08, "relationship_value": 0.0, "risk_score": 0.0},
            moderation=moderation,
            aggregation_info={"is_aggregated_event": False, "aggregation_kind": "", "aggregation_window_ms": 0, "member_count": 1, "sample_messages": []},
            metadata={"platform": "bilibili", "room_id": room_id, "room_state": str(raw.get("state", "room_state")), "popularity": popularity, **scene_stats},
            intervention_candidate=False,
            intervention_reason="",
        )
        return [self._emit(room, payload)]

    def flush_room(self, room_id: str, timestamp_ms: int) -> list[dict[str, Any]]:
        room = self.room(room_id)
        events: list[dict[str, Any]] = []
        aggregated = room.danmaku_aggregator.flush(timestamp_ms=timestamp_ms)
        if aggregated is not None:
            events.append(
                self._emit_aggregated_danmaku(
                    room,
                    aggregated=aggregated,
                    timestamp_ms=timestamp_ms,
                    scene_stats=room.stats_tracker.snapshot(timestamp_ms),
                )
            )
        for state in room.gift_combos.flush_expired(timestamp_ms):
            allow_reply = state.reply_candidates_emitted < self.config.combo.max_reply_candidates_per_combo
            if allow_reply:
                state.reply_candidates_emitted += 1
            room.metrics.combo_summary_emit_count += 1
            events.append(
                self._emit(
                    room,
                    self._gift_combo_event(
                        state,
                        timestamp_ms=timestamp_ms,
                        allow_reply=allow_reply,
                        source_type="bilibili_gift_combo_summary",
                        event_type="gift_combo_summary",
                        scene_stats=room.stats_tracker.snapshot(timestamp_ms),
                    ),
                )
            )
        return events

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(max(self.config.combo.combo_quiet_timeout_ms / 2000, 0.5))
            timestamp_ms = now_ms()
            for room_id in list(self._rooms):
                self.flush_room(room_id, timestamp_ms)

    def _create_room(self, room_id: str) -> RoomPipeline:
        metrics = GatewayMetrics()
        return RoomPipeline(
            room_id=room_id,
            config=self.config,
            metrics=metrics,
            store=EventStore(max_events=self.config.queue.max_events, cursor_retention=self.config.queue.cursor_retention),
            risk_tagger=RiskTagger(rule_file=self.config.risk.rule_file, default_reply_policy=self.config.risk.default_reply_policy),
            stats_tracker=SceneStatsTracker(window_ms=self.config.windows.scene_stats_window_ms),
            dedupe=DedupeTracker(window_ms=self.config.windows.dedupe_window_ms),
            danmaku_aggregator=DanmakuAggregator(window_ms=self.config.windows.aggregate_window_ms),
            gift_combos=GiftComboTracker(config=self.config.combo, metrics=metrics),
        )

    async def _connect_live_room(self, room: RoomPipeline) -> None:
        self.record_live_stage(
            room_id=room.room_id,
            stage="connect_mode",
            status="start",
            detail={"connection_mode": "live"},
        )
        room.connector = BLiveDMRoomConnector(
            room_id=room.room_id,
            owner_uid=self.config.room.owner_uid,
            cookies=self.config.auth.cookies(),
            service=self,
            skip_live_status_check=self.config.testing.live_connect_fallback_mode == "offline_history",
        )
        try:
            await room.connector.start()
        except OfflineDanmakuUnavailableError as exc:
            self.record_live_stage(
                room_id=room.room_id,
                stage="connect_mode",
                status="failed",
                detail={"error": str(exc), "exception_class": exc.__class__.__name__},
            )
            fallback_mode = self.config.testing.live_connect_fallback_mode
            if fallback_mode != "offline_history":
                self.logger.exception("bilibili gateway connect failed")
                raise RuntimeError(f"bilibili gateway connect failed: {exc}") from exc
            self.debug("connect", f"room={room.room_id} websocket unavailable, explicit fallback=offline_history", style="yellow")
            room.connection_mode = "offline_history"
            self.record_live_stage(
                room_id=room.room_id,
                stage="fallback",
                status="start",
                detail={"fallback_mode": fallback_mode, "reason": str(exc)},
            )
            await self._connect_offline_history_room(room)
            self.record_live_stage(
                room_id=room.room_id,
                stage="fallback",
                status="ok",
                detail={"fallback_mode": fallback_mode},
            )
        except Exception as exc:
            self.record_live_stage(
                room_id=room.room_id,
                stage="connect_mode",
                status="failed",
                detail={"error": str(exc), "exception_class": exc.__class__.__name__},
            )
            self.logger.exception("bilibili gateway connect failed")
            raise RuntimeError(f"bilibili gateway connect failed: {exc}") from exc
        else:
            self.record_live_stage(
                room_id=room.room_id,
                stage="connect_mode",
                status="ok",
                detail={"connection_mode": "live"},
            )

    async def _connect_offline_history_room(self, room: RoomPipeline) -> None:
        self.record_live_stage(
            room_id=room.room_id,
            stage="connect_mode",
            status="start",
            detail={"connection_mode": "offline_history"},
        )
        room.connector = OfflineHistoryRoomConnector(
            room_id=room.room_id,
            cookies=self.config.auth.cookies(),
            service=self,
        )
        try:
            await room.connector.start()
        except Exception as exc:
            self.record_live_stage(
                room_id=room.room_id,
                stage="connect_mode",
                status="failed",
                detail={"error": str(exc), "exception_class": exc.__class__.__name__},
            )
            self.logger.exception("bilibili gateway connect failed")
            raise RuntimeError(f"bilibili gateway connect failed: {exc}") from exc
        else:
            self.record_live_stage(
                room_id=room.room_id,
                stage="connect_mode",
                status="ok",
                detail={"connection_mode": "offline_history"},
            )

    def _connect_response(self, room: RoomPipeline) -> dict[str, Any]:
        return {
            "status": "connected",
            "room_id": room.room_id,
            "mock_mode": self.config.service.mock_mode,
            "connection_mode": room.connection_mode,
            "requested_connection_mode": room.requested_connection_mode,
            "live_diagnostics": dict(room.live_diagnostics or {}),
        }

    def _emit_aggregated_danmaku(
        self,
        room: RoomPipeline,
        *,
        aggregated: dict[str, Any],
        timestamp_ms: int,
        scene_stats: dict[str, str],
    ) -> dict[str, Any]:
        self.debug(
            "aggregate",
            f"room={room.room_id} danmaku_window member_count={aggregated['aggregation_info']['member_count']} content={aggregated['content_norm']}",
            style="magenta",
        )
        payload = aggregated_danmaku_payload(
            room_id=room.room_id,
            occurred_at=ms_to_iso(timestamp_ms),
            content_raw=str(aggregated["content_raw"]),
            content_norm=str(aggregated["content_norm"]),
            moderation=self._with_raw_content_ref(room.risk_tagger.tag(str(aggregated["content_norm"]))),
            aggregation_info=aggregated["aggregation_info"],
            metadata={"platform": "bilibili", "room_id": room.room_id, **scene_stats},
        )
        return self._emit(room, payload)

    def _gift_combo_event(
        self,
        state: GiftComboState,
        *,
        timestamp_ms: int,
        allow_reply: bool,
        source_type: str,
        event_type: str,
        scene_stats: dict[str, str],
    ) -> dict[str, Any]:
        moderation = self._with_raw_content_ref(
            {
                "moderation_labels": [],
                "semantic_summary": f"{state.username} 连送 {state.gift_name} 共 {state.count} 次",
                "reply_policy": "normal_reply" if allow_reply else "observe_only",
                "quote_allowed": True,
                "risk_score": 0.0,
            }
        )
        return build_event(
            event_type=event_type,
            source_type=source_type,
            event_kind="feedback",
            content_raw=f"{state.username} 连续送出 {state.gift_name} x{state.count}",
            content_norm=f"{state.username} 连送 {state.gift_name} x{state.count}",
            room_id=state.room_id,
            username=state.username,
            user_id=state.platform_user_id,
            occurred_at=ms_to_iso(timestamp_ms),
            target={"target_kind": "streamer", "target_entity_id": "self_main", "target_confidence": 0.95},
            message_value={
                "reply_value": min(1.0, 0.75 if allow_reply else 0.25),
                "show_value": GiftComboTracker.compute_show_value(state.total_value),
                "memory_value": 0.35,
                "relationship_value": 0.55,
                "risk_score": 0.0,
            },
            moderation=moderation,
            aggregation_info={
                "is_aggregated_event": True,
                "aggregation_kind": "gift_combo",
                "aggregation_window_ms": self.config.combo.combo_quiet_timeout_ms,
                "member_count": state.count,
                "sample_messages": [],
            },
            metadata={
                "platform": "bilibili",
                "combo_state": state.combo_state,
                "combo_session_id": state.combo_session_id,
                "combo_count": state.count,
                "combo_total_value": state.total_value,
                "combo_duration_ms": max(0, timestamp_ms - state.start_ms),
                "gift_id": state.gift_id,
                "gift_name": state.gift_name,
                "is_paid": True,
                "is_known_viewer": True,
                **scene_stats,
            },
            intervention_candidate=False,
            intervention_reason="",
        )

    def _emit(self, room: RoomPipeline, payload: dict[str, Any]) -> dict[str, Any]:
        payload.setdefault("trace_id", new_trace_id())
        payload.setdefault("event_id", new_event_id())
        payload.setdefault("room_id", room.room_id)
        payload.setdefault("occurred_at", utc_now_iso())
        payload.setdefault("identity_resolution", "unknown")
        metadata = payload.setdefault("metadata", {})
        metadata.setdefault("connection_mode", room.connection_mode)
        room.metrics.normalized_count += 1
        if payload.get("aggregation_info", {}).get("is_aggregated_event"):
            room.metrics.aggregated_count += 1
        if payload.get("moderation_view", {}).get("moderation_labels"):
            room.metrics.risk_event_count += 1
        if float(payload.get("message_value", {}).get("reply_value", 0.0)) >= 0.6:
            room.metrics.reply_candidate_count += 1
        room.store.append(payload)
        self._debug_emit(room.room_id, payload)
        return payload

    def _with_raw_content_ref(self, moderation: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(moderation)
        enriched.setdefault("raw_content_ref", new_raw_content_ref())
        return enriched

    def debug(self, stage: str, message: str, *, style: str = "white") -> None:
        if not self._debug_enabled:
            return
        text = Text()
        text.append(f"[{stage}] ", style=f"bold {style}")
        text.append(message, style=style)
        _CONSOLE.print(text)

    def _debug_raw(self, kind: str, room_id: str, raw: dict[str, Any], *, accent: str) -> None:
        if not self._debug_enabled:
            return
        pretty = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True)
        _CONSOLE.print(Panel.fit(pretty, title=f"{kind} RAW room={room_id}", border_style=accent))

    def _debug_emit(self, room_id: str, payload: dict[str, Any]) -> None:
        if not self._debug_enabled:
            return
        summary = {
            "type": payload.get("source_type"),
            "content_norm": payload.get("content_norm"),
            "target_kind": payload.get("target_kind"),
            "reply_value": payload.get("message_value", {}).get("reply_value"),
            "show_value": payload.get("message_value", {}).get("show_value"),
            "connection_mode": payload.get("metadata", {}).get("connection_mode"),
            "aggregation_info": payload.get("aggregation_info"),
            "moderation_view": payload.get("moderation_view"),
            "metadata": {
                key: payload.get("metadata", {}).get(key)
                for key in (
                    "combo_state",
                    "combo_count",
                    "combo_total_value",
                    "combo_duration_ms",
                    "risk_level",
                    "danmaku_velocity",
                    "audience_density",
                    "engagement_level",
                )
                if key in payload.get("metadata", {})
            },
        }
        _CONSOLE.print(
            Panel.fit(
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
                title=f"EMIT room={room_id}",
                border_style="green",
            )
        )

    def _resolve_connection_mode(self, requested_mode: str | None) -> str:
        normalized = str(requested_mode or "").strip().lower()
        if not normalized:
            if self.config.service.mock_mode:
                return "mock"
            return "live"
        if normalized == "mock":
            if not self.config.service.mock_mode:
                raise ValueError("connection_mode=mock requires service.mock_mode = true")
            return "mock"
        if normalized == "live":
            if self.config.service.mock_mode:
                raise ValueError("connection_mode=live is unavailable while service.mock_mode = true")
            return "live"
        if normalized == "offline_history":
            if self.config.service.mock_mode:
                raise ValueError("connection_mode=offline_history is unavailable while service.mock_mode = true")
            if not self.config.testing.offline_test_mode_enabled:
                raise ValueError("connection_mode=offline_history requires [gateway.testing].offline_test_mode_enabled = true")
            return "offline_history"
        if normalized == "offline_test":
            if not self.config.testing.offline_test_mode_enabled:
                raise ValueError("connection_mode=offline_test is disabled; set [gateway.testing].offline_test_mode_enabled = true")
            return "offline_test"
        raise ValueError(f"unsupported connection_mode: {requested_mode}")

    def record_live_heartbeat(self, *, room_id: str, popularity: int) -> None:
        room = self.room(room_id)
        diagnostics = self._ensure_live_diagnostics(room)
        diagnostics["heartbeat_count"] = int(diagnostics.get("heartbeat_count", 0)) + 1
        diagnostics["last_heartbeat_at_ms"] = now_ms()
        diagnostics["last_popularity"] = popularity
        self._write_live_debug_dump(room_id)
        self.debug("live_heartbeat", f"room={room_id} popularity={popularity} heartbeat_count={diagnostics['heartbeat_count']}", style="bright_blue")

    def record_live_business_message(self, *, room_id: str, event_type: str) -> None:
        room = self.room(room_id)
        diagnostics = self._ensure_live_diagnostics(room)
        diagnostics["business_message_count"] = int(diagnostics.get("business_message_count", 0)) + 1
        diagnostics["last_business_event_type"] = event_type
        diagnostics["last_business_message_at_ms"] = now_ms()
        self._write_live_debug_dump(room_id)
        self.debug(
            "live_event",
            f"room={room_id} business_event={event_type} business_message_count={diagnostics['business_message_count']}",
            style="bright_cyan",
        )

    def record_live_stop(self, *, room_id: str, exception: Exception | None) -> None:
        room = self.room(room_id)
        diagnostics = self._ensure_live_diagnostics(room)
        diagnostics["stop_count"] = int(diagnostics.get("stop_count", 0)) + 1
        diagnostics["last_stop_at_ms"] = now_ms()
        diagnostics["last_stop_reason"] = "" if exception is None else repr(exception)
        diagnostics["last_stop_exception_class"] = "" if exception is None else exception.__class__.__name__
        heartbeat_count = int(diagnostics.get("heartbeat_count", 0))
        business_count = int(diagnostics.get("business_message_count", 0))
        if exception is None and heartbeat_count > 0 and business_count == 0:
            diagnostics["last_stop_classification"] = "heartbeat_only_no_business_messages"
        elif exception is None:
            diagnostics["last_stop_classification"] = "stopped_without_exception"
        else:
            diagnostics["last_stop_classification"] = "stopped_with_exception"
        self._write_live_debug_dump(room_id)
        if exception is None:
            self.debug(
                "live_stop",
                f"room={room_id} websocket stopped stop_classification={diagnostics['last_stop_classification']}",
                style="yellow",
            )
            return
        self.debug(
            "live_stop",
            f"room={room_id} websocket stopped exception_class={exception.__class__.__name__} detail={exception!r}",
            style="red",
        )

    def record_live_stage(self, *, room_id: str, stage: str, status: str, detail: dict[str, Any] | None = None) -> None:
        room = self.room(room_id)
        diagnostics = self._ensure_live_diagnostics(room)
        stages = diagnostics.setdefault("stages", [])
        if isinstance(stages, list):
            stages.append(
                {
                    "stage": stage,
                    "status": status,
                    "timestamp_ms": now_ms(),
                    "detail": dict(detail or {}),
                }
            )
            if len(stages) > 64:
                del stages[:-64]
        diagnostics["last_stage"] = stage
        diagnostics["last_stage_status"] = status
        if detail:
            for key, value in detail.items():
                diagnostics[key] = value
        self._write_live_debug_dump(room_id)
        detail_text = ""
        if detail:
            detail_text = " " + " ".join(f"{key}={value}" for key, value in detail.items())
        self.debug("live_stage", f"room={room_id} stage={stage} status={status}{detail_text}", style=self._stage_style(status))

    def _initial_live_diagnostics(self, *, room_id: str, requested_mode: str) -> dict[str, Any]:
        return {
            "room_id": room_id,
            "requested_connection_mode": requested_mode,
            "effective_connection_mode": requested_mode,
            "live_status": None,
            "login_uid": None,
            "host_server_count": 0,
            "host_server_token_ready": False,
            "heartbeat_count": 0,
            "business_message_count": 0,
            "last_business_event_type": "",
            "last_business_message_at_ms": None,
            "last_heartbeat_at_ms": None,
            "last_popularity": 0,
            "last_stop_reason": "",
            "last_stop_exception_class": "",
            "last_stop_classification": "",
            "stop_count": 0,
            "stages": [],
        }

    def _ensure_live_diagnostics(self, room: RoomPipeline) -> dict[str, Any]:
        if room.live_diagnostics is None:
            room.live_diagnostics = self._initial_live_diagnostics(
                room_id=room.room_id,
                requested_mode=room.requested_connection_mode,
            )
        room.live_diagnostics["effective_connection_mode"] = room.connection_mode
        room.live_diagnostics["requested_connection_mode"] = room.requested_connection_mode
        return room.live_diagnostics

    def _write_live_debug_dump(self, room_id: str) -> None:
        if not self._debug_enabled:
            return
        room = self.room(room_id)
        target_dir = self._debug_dump_dir / room_id
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "room_id": room.room_id,
            "connected": room.connected,
            "connected_at": room.connected_at,
            "connection_mode": room.connection_mode,
            "requested_connection_mode": room.requested_connection_mode,
            "live_diagnostics": dict(room.live_diagnostics or {}),
        }
        (target_dir / "live_diagnostics.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @staticmethod
    def _stage_style(status: str) -> str:
        normalized = status.strip().lower()
        if normalized in {"failed", "error"}:
            return "red"
        if normalized in {"degraded", "warn"}:
            return "yellow"
        if normalized in {"ok", "recovered"}:
            return "green"
        return "cyan"

    def _engagement_signal_for_danmaku(self, *, text: str, target_kind: str, reply_value: float) -> float:
        signal = 0.0
        if target_kind == "streamer":
            signal += 0.7
        if any(marker in text for marker in ("?", "？", "吗", "么", "啥", "什么", "为什么")):
            signal += 0.8
        if reply_value >= 0.7:
            signal += 1.0
        elif reply_value >= 0.45:
            signal += 0.45
        if len(text.strip()) >= 8:
            signal += 0.25
        return signal


__all__ = [
    "BLiveDMHandler",
    "BLiveDMRoomConnector",
    "BilibiliGatewayService",
    "OfflineDanmakuUnavailableError",
    "OfflineHistoryRoomConnector",
    "RoomConnector",
    "RoomPipeline",
    "now_ms",
]
