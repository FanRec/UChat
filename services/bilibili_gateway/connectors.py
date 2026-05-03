from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Any, Protocol

import aiohttp
import blivedm


class RoomConnector(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class OfflineDanmakuUnavailableError(RuntimeError):
    pass


class BLiveDMHandler(blivedm.BaseHandler):
    def __init__(self, service: Any, room_id: str) -> None:
        super().__init__()
        self.service = service
        self.room_id = room_id

    def _mark_business_activity(self, event_type: str) -> None:
        self.service.record_live_business_message(room_id=self.room_id, event_type=event_type)

    def _on_danmaku(self, client: blivedm.BLiveClient, message: blivedm.DanmakuMessage) -> None:
        self._mark_business_activity("danmaku")
        self.service.ingest_danmaku(
            room_id=self.room_id,
            raw={
                "msg": message.msg,
                "uid": message.uid,
                "uname": message.uname,
                "timestamp": int(message.timestamp) * 1000 if int(message.timestamp) < 10_000_000_000 else int(message.timestamp),
            },
        )

    def _on_gift(self, client: blivedm.BLiveClient, message: blivedm.GiftMessage) -> None:
        self._mark_business_activity("gift")
        self.service.ingest_gift(
            room_id=self.room_id,
            raw={
                "uid": message.uid,
                "uname": message.uname,
                "gift_id": message.gift_id,
                "gift_name": message.gift_name,
                "num": message.num,
                "price": message.price,
                "total_coin": message.total_coin,
                "tid": message.tid,
                "rnd": message.rnd,
                "timestamp": int(message.timestamp) * 1000 if int(message.timestamp) < 10_000_000_000 else int(message.timestamp),
            },
        )

    def _on_super_chat(self, client: blivedm.BLiveClient, message: blivedm.SuperChatMessage) -> None:
        self._mark_business_activity("super_chat")
        self.service.ingest_super_chat(
            room_id=self.room_id,
            raw={
                "uid": message.uid,
                "uname": message.uname,
                "message": message.message,
                "price": message.price,
                "gift_id": message.gift_id,
                "gift_name": message.gift_name,
                "id": message.id,
            },
        )

    def _on_buy_guard(self, client: blivedm.BLiveClient, message: blivedm.GuardBuyMessage) -> None:
        self._mark_business_activity("buy_guard")
        self.service.ingest_follow(
            room_id=self.room_id,
            raw={
                "uid": message.uid,
                "uname": message.username,
                "guard_level": message.guard_level,
                "num": message.num,
                "price": message.price,
                "gift_id": message.gift_id,
                "gift_name": message.gift_name,
            },
        )

    def _on_heartbeat(self, client: blivedm.BLiveClient, message: blivedm.HeartbeatMessage) -> None:
        self.service.record_live_heartbeat(room_id=self.room_id, popularity=int(message.popularity))
        self.service.ingest_room_state(
            room_id=self.room_id,
            raw={"state": "heartbeat", "popularity": int(message.popularity)},
        )

    def on_client_stopped(self, client: blivedm.BLiveClient, exception: Exception | None):
        self.service.record_live_stop(room_id=self.room_id, exception=exception)


class BLiveDMRoomConnector:
    def __init__(
        self,
        *,
        room_id: str,
        owner_uid: int,
        cookies: dict[str, str],
        service: Any,
        skip_live_status_check: bool = False,
    ) -> None:
        self.room_id = room_id
        self.owner_uid = owner_uid
        self.cookies = cookies
        self.service = service
        self.skip_live_status_check = skip_live_status_check
        self.last_live_status: int | None = None
        self.login_uid: int | None = None
        self.host_server_count: int = 0
        self.host_server_token_ready: bool = False
        self._session: aiohttp.ClientSession | None = None
        self._client: blivedm.BLiveClient | None = None

    async def start(self) -> None:
        start_ms = int(time.time() * 1000)
        self.service.record_live_stage(
            room_id=self.room_id,
            stage="session_create",
            status="start",
            detail={"skip_live_status_check": self.skip_live_status_check},
        )
        self._session = aiohttp.ClientSession(
            cookies=self.cookies or None,
            headers={"Accept-Encoding": "gzip, deflate", "User-Agent": "UChat-bilibili-gateway/0.1"},
            skip_auto_headers={"Accept-Encoding"},
        )
        try:
            self.service.record_live_stage(room_id=self.room_id, stage="room_init_check", status="start")
            live_status = await self._ensure_room_is_live()
            self.service.record_live_stage(
                room_id=self.room_id,
                stage="room_init_check",
                status="ok",
                detail={"live_status": live_status},
            )
            if live_status != 1 and self.skip_live_status_check:
                raise OfflineDanmakuUnavailableError(
                    f"room {self.room_id} is offline (live_status={live_status}); "
                    "skip live websocket reconnect storm and use offline history testing instead"
                )
            self.login_uid = int(self.owner_uid) if int(self.owner_uid or 0) > 0 else None
            self.service.record_live_stage(
                room_id=self.room_id,
                stage="client_create",
                status="start",
                detail={"login_uid": self.login_uid},
            )
            self._client = blivedm.BLiveClient(int(self.room_id), uid=self.login_uid, session=self._session)
            self._client.set_handler(BLiveDMHandler(self.service, self.room_id))
            self.service.record_live_stage(room_id=self.room_id, stage="client_init_room", status="start")
            init_ok = await self._client.init_room()
            self.host_server_count = len(getattr(self._client, "_host_server_list", None) or [])
            self.host_server_token_ready = bool(getattr(self._client, "_host_server_token", None))
            self.service.record_live_stage(
                room_id=self.room_id,
                stage="client_init_room",
                status="ok" if init_ok else "degraded",
                detail={
                    "init_ok": init_ok,
                    "resolved_room_id": getattr(self._client, "room_id", None),
                    "room_owner_uid": getattr(self._client, "room_owner_uid", None),
                    "uid": getattr(self._client, "uid", None),
                    "host_server_count": self.host_server_count,
                    "host_server_token_ready": self.host_server_token_ready,
                },
            )
            if not init_ok:
                raise OfflineDanmakuUnavailableError(
                    f"bilibili danmaku init_room failed for room {self.room_id}; "
                    "the platform may not expose danmaku server config while offline"
                )
            self.service.record_live_stage(
                room_id=self.room_id,
                stage="websocket_start",
                status="start",
                detail={"elapsed_ms": int(time.time() * 1000) - start_ms},
            )
            self._client.start()
            self.service.record_live_stage(
                room_id=self.room_id,
                stage="websocket_start",
                status="ok",
                detail={"elapsed_ms": int(time.time() * 1000) - start_ms},
            )
        except Exception:
            await self.stop()
            raise

    async def _ensure_room_is_live(self) -> int:
        if self._session is None:
            raise RuntimeError("bilibili session not initialized")
        async with self._session.get(
            "https://api.live.bilibili.com/room/v1/Room/room_init",
            params={"id": self.room_id},
            ssl=True,
        ) as res:
            if res.status != 200:
                raise RuntimeError(f"room_init failed with status {res.status}")
            body = await res.json()
            if int(body.get("code", -1)) != 0:
                raise RuntimeError(f"room_init failed with code {body.get('code')}")
            data = dict(body.get("data") or {})
            live_status = int(data.get("live_status", 0) or 0)
            self.last_live_status = live_status
            if live_status != 1 and not self.skip_live_status_check:
                raise RuntimeError(
                    f"room {self.room_id} is not live (live_status={live_status}); start the stream before danmaku integration"
                )
            if live_status != 1 and self.skip_live_status_check:
                self.service.debug("connect", f"room={self.room_id} room_init live_status={live_status} but skip_live_status_check=true", style="yellow")
            return live_status

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.stop_and_close()
            self._client = None
        if self._session is not None:
            await self._session.close()
            self._session = None


class OfflineHistoryRoomConnector:
    HISTORY_URL = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"

    def __init__(self, *, room_id: str, cookies: dict[str, str], service: Any, poll_interval_s: float = 1.5) -> None:
        self.room_id = room_id
        self.cookies = cookies
        self.service = service
        self.poll_interval_s = poll_interval_s
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._seen_keys: deque[str] = deque(maxlen=512)
        self._seen_lookup: set[str] = set()

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            cookies=self.cookies or None,
            headers={"Accept-Encoding": "gzip, deflate", "User-Agent": "UChat-bilibili-gateway/0.1"},
            skip_auto_headers={"Accept-Encoding"},
        )
        baseline = await self._fetch_history()
        self._remember_entries(baseline)
        self._task = asyncio.create_task(self._poll_loop(), name=f"bilibili-offline-history-{self.room_id}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval_s)
            entries = await self._fetch_history()
            new_entries = [entry for entry in entries if not self._seen(entry)]
            if not new_entries:
                continue
            self._remember_entries(new_entries)
            for entry in reversed(new_entries):
                self.service.ingest_danmaku(
                    room_id=self.room_id,
                    raw={
                        "uid": str(entry.get("uid") or entry.get("user", {}).get("uid") or ""),
                        "uname": str(entry.get("nickname") or entry.get("uname") or ""),
                        "msg": str(entry.get("text") or "").strip(),
                        "timestamp": self.service.now_ms(),
                    },
                )

    async def _fetch_history(self) -> list[dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("offline history session not initialized")
        async with self._session.get(self.HISTORY_URL, params={"roomid": self.room_id}, ssl=True) as res:
            if res.status != 200:
                raise RuntimeError(f"offline history failed with status {res.status}")
            body = await res.json()
            if int(body.get("code", -1)) != 0:
                raise RuntimeError(f"offline history failed with code {body.get('code')}")
            data = dict(body.get("data") or {})
            entries: list[dict[str, Any]] = []
            for bucket in ("admin", "room"):
                items = data.get(bucket) or []
                if isinstance(items, list):
                    entries.extend(item for item in items if isinstance(item, dict))
            return entries

    def _remember_entries(self, entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            key = self._entry_key(entry)
            if key in self._seen_lookup:
                continue
            if len(self._seen_keys) == self._seen_keys.maxlen:
                stale = self._seen_keys.popleft()
                self._seen_lookup.discard(stale)
            self._seen_keys.append(key)
            self._seen_lookup.add(key)

    def _seen(self, entry: dict[str, Any]) -> bool:
        return self._entry_key(entry) in self._seen_lookup

    @staticmethod
    def _entry_key(entry: dict[str, Any]) -> str:
        stable = str(entry.get("id_str") or "").strip()
        if stable:
            return stable
        return ":".join(
            [
                str(entry.get("uid") or entry.get("user", {}).get("uid") or ""),
                str(entry.get("rnd") or ""),
                str(entry.get("timeline") or ""),
                str(entry.get("text") or ""),
            ]
        )
