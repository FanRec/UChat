from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.obs_bridge.config import ObsBridgeConfig
from services.obs_bridge.service import ObsBridgeService, _debug_info, _debug_ws, _debug_error


class SubtitleRequest(BaseModel):
    trace_id: str = ""
    outbound_id: str = ""
    task_id: str = ""
    channel: str = "subtitle"
    destination: str = "obs"
    text: str = ""
    action: str = "sentence"
    metadata: dict[str, Any] = {}


class StatusRequest(BaseModel):
    trace_id: str = ""
    outbound_id: str = ""
    text: str = ""
    channel: str = "status"
    destination: str = "obs"
    metadata: dict[str, Any] = {}


class CancelRequest(BaseModel):
    task_id: str = ""
    trace_id: str = ""


def create_app(service: ObsBridgeService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _debug_info(
            f"obs_bridge ready on {service.config.service.listen_host}:{service.config.service.listen_port}\n"
            f"overlay: {service.overlay_config.position}  style={service.overlay_config.style_preset}\n"
            f"ws endpoint: ws://{service.config.service.listen_host}:{service.config.service.listen_port}/ws/subtitle"
        )
        yield
        _debug_info("obs_bridge shutting down")

    app = FastAPI(title="obs_bridge", lifespan=lifespan)
    app.state.service = service

    # ── HTTP routes ────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return service.health()

    @app.post("/v1/obs/subtitle")
    async def post_subtitle(req: SubtitleRequest):
        body = req.model_dump()
        return service.handle_subtitle(body)

    @app.post("/v1/obs/status")
    async def post_status(req: StatusRequest):
        body = req.model_dump()
        return service.handle_status(body)

    @app.post("/v1/obs/cancel")
    async def post_cancel(req: CancelRequest):
        body = req.model_dump()
        return service.handle_cancel(body)

    # ── WebSocket route ────────────────────────────────────────────

    @app.websocket("/ws/subtitle")
    async def ws_subtitle(ws: WebSocket):
        await ws.accept()
        svc: ObsBridgeService = ws.app.state.service
        svc.register_ws(ws)
        # 重放当前活跃字幕
        for item in svc.get_active_subtitles():
            try:
                await ws.send_json(item)
            except Exception:
                break
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            svc.unregister_ws(ws)

    # ── Static overlay ─────────────────────────────────────────────

    overlay_dir = Path(__file__).resolve().parent / "overlay"
    if overlay_dir.is_dir():
        app.mount("/overlay", StaticFiles(directory=str(overlay_dir), html=True), name="overlay")

    return app
