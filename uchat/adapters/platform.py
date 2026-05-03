from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from uchat.adapters import InputAdapter, OutputAdapter
from uchat.contracts import DeliveryReceipt, NormalizedEvent, OutboundEvent, new_event_id, new_trace_id, utc_now_iso
from uchat.contracts.gateway import GatewayInputEvent


class BilibiliLiveInputAdapter(InputAdapter):
    def __init__(
        self,
        *,
        scene_id: str,
        session_window_id: str,
        room_id: str = "",
        base_url: str = "",
        reconnect_backoff_ms: int = 1000,
        preferred_connection_mode: str = "",
        client: httpx.Client | None = None,
    ):
        self.scene_id = scene_id
        self.session_window_id = session_window_id
        self.room_id = room_id
        self.base_url = base_url.rstrip("/")
        self.reconnect_backoff_ms = reconnect_backoff_ms
        self._preferred_connection_mode = preferred_connection_mode.strip().lower()
        self.client = client or (httpx.Client(base_url=self.base_url, trust_env=False) if self.base_url else None)
        self._sink: Callable[[NormalizedEvent], None] | None = None
        self._cursor = ""
        self._connected = False
        self._connection_mode = ""

    def start(self) -> None:
        self.connect()

    def stop(self) -> None:
        self.disconnect()

    def set_event_sink(self, sink: Callable[[NormalizedEvent], None] | None) -> None:
        self._sink = sink

    def connect(self) -> None:
        if self.client is None:
            self._connected = True
            return
        payload: dict[str, Any] = {}
        if self.room_id:
            payload["room_id"] = self.room_id
        if self._preferred_connection_mode:
            payload["connection_mode"] = self._preferred_connection_mode
        response = self.client.post(
            "/v1/bilibili/connect",
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(f"Bilibili connect failed: {response.status_code} {response.text}")
        body = response.json() if response.content else {}
        self._connection_mode = str(body.get("connection_mode", ""))
        self._connected = True

    def disconnect(self) -> None:
        if self.client is not None and self._connected:
            payload: dict[str, Any] = {}
            if self.room_id:
                payload["room_id"] = self.room_id
            self.client.post(
                "/v1/bilibili/disconnect",
                content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
        self._connected = False
        self._connection_mode = ""

    def build_danmaku_event(
        self,
        *,
        text: str,
        username: str,
        user_id: str,
        is_paid: bool = False,
        is_known_viewer: bool = False,
        room_id: str = "",
        danmaku_velocity: str = "normal",
        audience_density: str = "normal",
        risk_level: str = "L1",
        program_topic: str = "",
        segment_topic: str = "",
        micro_topic: str = "",
    ) -> NormalizedEvent:
        event = NormalizedEvent.from_bilibili_danmaku(
            text=text,
            scene_id=self.scene_id,
            session_window_id=self.session_window_id,
            username=username,
            user_id=user_id,
            is_paid=is_paid,
            is_known_viewer=is_known_viewer,
            room_id=room_id or self.room_id,
        )
        event.metadata.update(
            {
                "danmaku_velocity": danmaku_velocity,
                "audience_density": audience_density,
                "risk_level": risk_level,
                "program_topic": program_topic,
                "segment_topic": segment_topic,
                "micro_topic": micro_topic,
            }
        )
        return event

    def build_room_state_event(
        self,
        *,
        state: str,
        title: str = "",
        program_topic: str = "",
        segment_topic: str = "",
        micro_topic: str = "",
        risk_level: str = "L1",
    ) -> NormalizedEvent:
        return NormalizedEvent(
            event_id=new_event_id(),
            trace_id=new_trace_id(),
            occurred_at=utc_now_iso(),
            event_kind="system",
            source_type="bilibili_room_state",
            scene_id=self.scene_id,
            session_window_id=self.session_window_id,
            content_raw=state,
            content_norm=state,
            metadata={
                "origin_system": "uchat",
                "platform": "bilibili",
                "room_id": self.room_id,
                "room_state": state,
                "title": title,
                "program_topic": program_topic,
                "segment_topic": segment_topic,
                "micro_topic": micro_topic,
                "risk_level": risk_level,
            },
        )

    def build_gateway_event(self, item: dict[str, Any]) -> NormalizedEvent:
        payload = GatewayInputEvent.from_dict(dict(item))
        event = NormalizedEvent.from_gateway_payload(
            payload=payload,
            scene_id=self.scene_id,
            session_window_id=self.session_window_id,
        )
        event.metadata.setdefault("platform_event_id", payload.event_id)
        return event

    def emit_danmaku(self, **kwargs: str | bool) -> NormalizedEvent:
        event = self.build_danmaku_event(**kwargs)
        if self._sink is not None:
            self._sink(event)
        return event

    def poll_events(self, *, limit: int = 20) -> list[NormalizedEvent]:
        if self.client is None:
            return []
        if not self._connected:
            self.connect()
        params: dict[str, Any] = {"cursor": self._cursor, "limit": limit}
        if self.room_id:
            params["room_id"] = self.room_id
        try:
            response = self.client.get("/v1/bilibili/events", params=params)
        except httpx.HTTPError:
            self._connected = False
            time.sleep(max(self.reconnect_backoff_ms, 0) / 1000)
            self.connect()
            response = self.client.get("/v1/bilibili/events", params=params)
        if response.status_code != 200:
            raise RuntimeError(f"Bilibili poll failed: {response.status_code} {response.text}")
        body = response.json() if response.content else {}
        self._cursor = str(body.get("next_cursor", self._cursor))
        normalized: list[NormalizedEvent] = []
        for item in body.get("events", []):
            if not isinstance(item, dict):
                continue
            event = self.build_gateway_event(item)
            normalized.append(event)
            if self._sink is not None:
                self._sink(event)
        return normalized


class BilibiliOutputAdapter(OutputAdapter):
    def __init__(
        self,
        *,
        base_url: str,
        room_id: str,
        client: httpx.Client | None = None,
        min_send_interval_ms: int = 800,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.room_id = room_id
        self.client = client or httpx.Client(base_url=self.base_url, trust_env=False)
        self.min_send_interval_ms = min_send_interval_ms
        self._last_sent_at_ms = 0.0

    def send(self, event: OutboundEvent) -> DeliveryReceipt:
        now_ms = time.perf_counter() * 1000
        if self._last_sent_at_ms and now_ms - self._last_sent_at_ms < self.min_send_interval_ms:
            return DeliveryReceipt.failed(
                trace_id=event.trace_id,
                task_id=str(event.metadata.get("task_id", event.outbound_id)),
                channel=event.channel,
                destination=event.destination,
                detail={"reason": "rate_limited", "min_send_interval_ms": self.min_send_interval_ms},
            )
        response = self.client.post(
            "/v1/bilibili/messages",
            json={
                "room_id": self.room_id,
                "trace_id": event.trace_id,
                "outbound_id": event.outbound_id,
                "text": event.text,
                "channel": event.channel,
                "destination": event.destination,
                "metadata": event.metadata,
            },
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(f"Bilibili send failed: {response.status_code} {response.text}")
        self._last_sent_at_ms = now_ms
        body = response.json() if response.content else {}
        return DeliveryReceipt.delivered(
            trace_id=event.trace_id,
            task_id=str(event.metadata.get("task_id", event.outbound_id)),
            channel=event.channel,
            destination=event.destination,
            detail={"service_status": body.get("status", "accepted")},
            metadata={"platform_message_id": body.get("message_id")},
        )

    def ack_delivery(self, receipt: DeliveryReceipt) -> dict:
        response = self.client.post(
            "/v1/bilibili/messages/ack",
            json={
                "trace_id": receipt.trace_id,
                "task_id": receipt.task_id,
                "status": receipt.status,
                "platform_message_id": receipt.metadata.get("platform_message_id"),
            },
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(f"Bilibili ack failed: {response.status_code} {response.text}")
        return response.json() if response.content else {"status": "accepted"}

    def close(self) -> None:
        self.client.close()
