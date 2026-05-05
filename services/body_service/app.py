from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from services.body_service.service import BodyService


class CommandRequest(BaseModel):
    command_id: str = ""
    trace_id: str = ""
    body_id: str = ""
    command_type: str = "speech_plan"
    generation_id: int = 1
    segment_index: int | None = None
    expression: str | None = None
    motion: str | None = None
    intensity: float = 0.5
    duration_ms: int = 0
    sync_to_audio: bool = False
    commit_mode: str = "transient"
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpeechEventRequest(BaseModel):
    trace_id: str = ""
    task_id: str = ""
    action: str = "segment_start"
    generation_id: int = 1
    segment_index: int | None = None
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelTraceRequest(BaseModel):
    trace_id: str


def create_app(service: BodyService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.start()
        yield
        service.close()

    app = FastAPI(title="body_service", lifespan=lifespan)
    app.state.service = service

    @app.get("/health")
    async def health():
        return service.health()

    @app.get("/v1/body/state")
    async def state():
        return service.state()

    @app.post("/v1/body/command")
    async def post_command(req: CommandRequest):
        return service.handle_command(req.model_dump())

    @app.post("/v1/body/speech-event")
    async def post_speech_event(req: SpeechEventRequest):
        return service.handle_speech_event(req.model_dump())

    @app.post("/v1/body/cancel-trace")
    async def post_cancel_trace(req: CancelTraceRequest):
        return service.cancel_trace(req.trace_id)

    return app
