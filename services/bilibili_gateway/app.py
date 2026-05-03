from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from services.bilibili_gateway.config import GatewayServiceConfig, load_service_config
from services.bilibili_gateway.service import BilibiliGatewayService


def create_app(config: GatewayServiceConfig | None = None) -> FastAPI:
    service_config = config or load_service_config()
    gateway = BilibiliGatewayService(service_config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await gateway.start_background_tasks()
        try:
            yield
        finally:
            await gateway.stop_background_tasks()

    app = FastAPI(title="UChat bilibili_gateway", lifespan=lifespan)
    app.state.gateway = gateway
    app.state.gateway_config = service_config

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": service_config.service.service_name,
            "mock_mode": service_config.service.mock_mode,
        }

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        return {
            "status": "ready",
            "service": service_config.service.service_name,
            "room_id": service_config.room.room_id,
            "offline_test_mode_enabled": service_config.testing.offline_test_mode_enabled,
        }

    @app.post("/v1/bilibili/connect")
    async def connect(payload: dict[str, Any]) -> dict[str, Any]:
        room_id = str(payload.get("room_id") or service_config.room.room_id)
        connection_mode = str(payload.get("connection_mode") or "").strip() or None
        try:
            return await gateway.connect(room_id, connection_mode=connection_mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/bilibili/disconnect")
    async def disconnect(payload: dict[str, Any]) -> dict[str, Any]:
        room_id = str(payload.get("room_id") or service_config.room.room_id)
        return await gateway.disconnect(room_id)

    @app.get("/v1/bilibili/events")
    async def events(room_id: str = "", cursor: str = "", limit: int = 0) -> dict[str, Any]:
        resolved_room_id = room_id or service_config.room.room_id
        try:
            return gateway.poll_events(room_id=resolved_room_id, cursor=cursor, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/bilibili/test-events")
    async def test_events(payload: dict[str, Any]) -> dict[str, Any]:
        room_id = str(payload.get("room_id") or service_config.room.room_id)
        event_type = str(payload.get("event_type") or payload.get("type") or "").strip()
        raw = payload.get("raw") or {}
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="payload.raw must be an object")
        try:
            emitted = gateway.ingest_test_event(room_id=room_id, event_type=event_type, raw=raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        room = gateway.room(room_id)
        return {
            "status": "accepted",
            "room_id": room.room_id,
            "connection_mode": room.connection_mode,
            "emitted_count": len(emitted),
            "event_ids": [str(item.get("event_id", "")) for item in emitted],
        }

    return app


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "service.toml"
app = create_app(load_service_config(DEFAULT_CONFIG_PATH))
