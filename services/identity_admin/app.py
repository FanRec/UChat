from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from services.identity_admin.config import IdentityAdminServiceConfig, load_service_config
from uchat.config import Settings
from uchat.identity import IdentityError, IdentityService, InMemoryIdentityStore, SQLiteIdentityStore
from uchat.identity.commands import (
    BindAccountCommand,
    ConsumeChallengeCommand,
    IssueChallengeCommand,
    RenamePersonCommand,
    bind_account,
    consume_challenge,
    get_account_identity,
    get_person_snapshot,
    issue_challenge,
    rename_person,
)


def create_app(
    config: IdentityAdminServiceConfig | None = None,
    *,
    settings: Settings | None = None,
    identity_service: IdentityService | None = None,
) -> FastAPI:
    service_config = config or load_service_config()
    runtime_settings = settings or Settings.load()
    service = identity_service or _build_identity_service(runtime_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="UChat identity_admin", lifespan=lifespan)
    app.state.identity_admin_config = service_config
    app.state.identity_settings = runtime_settings
    app.state.identity_service = service

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": service_config.service.service_name}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        return {
            "status": "ready",
            "service": service_config.service.service_name,
            "store_type": runtime_settings.identity.store_type,
            "sqlite_path": str(runtime_settings.identity.sqlite_path),
        }

    @app.post("/v1/identity/challenges")
    async def create_challenge(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            challenge = issue_challenge(
                service,
                IssueChallengeCommand(
                    platform=_required_str(payload, "platform"),
                    platform_user_id=_required_str(payload, "platform_user_id"),
                ),
            )
        except IdentityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return asdict(challenge)

    @app.post("/v1/identity/challenges/consume")
    async def consume_challenge_route(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            context = consume_challenge(
                service,
                ConsumeChallengeCommand(
                    platform=_required_str(payload, "platform"),
                    platform_user_id=_required_str(payload, "platform_user_id"),
                    platform_nickname=_required_str(payload, "platform_nickname"),
                    code=_required_str(payload, "code"),
                    binding_evidence=_binding_evidence(payload.get("binding_evidence")),
                ),
            )
        except IdentityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(context)

    @app.post("/v1/identity/bind")
    async def bind_route(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            account, context = bind_account(
                service,
                BindAccountCommand(
                    platform=_required_str(payload, "platform"),
                    platform_user_id=_required_str(payload, "platform_user_id"),
                    person_id=_required_str(payload, "person_id"),
                    resolution=_resolution(payload.get("resolution", "probable")),
                ),
            )
        except IdentityError as exc:
            message = str(exc)
            status_code = 404 if "not found" in message else 409
            raise HTTPException(status_code=status_code, detail=message) from exc
        return {"account": asdict(account), "identity_context": asdict(context)}

    @app.post("/v1/identity/rename")
    async def rename_route(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            person = rename_person(
                service,
                RenamePersonCommand(
                    person_id=_required_str(payload, "person_id"),
                    display_name=_required_str(payload, "display_name"),
                ),
            )
        except IdentityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return asdict(person)

    @app.get("/v1/identity/accounts/{platform}/{platform_user_id}")
    async def get_account_route(platform: str, platform_user_id: str) -> dict[str, Any]:
        try:
            account, context = get_account_identity(service, platform, platform_user_id)
        except IdentityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"account": asdict(account), "identity_context": asdict(context)}

    @app.get("/v1/identity/persons/{person_id}")
    async def get_person_route(person_id: str) -> dict[str, Any]:
        try:
            return get_person_snapshot(service, person_id)
        except IdentityError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


def _build_identity_service(settings: Settings) -> IdentityService:
    if settings.identity.store_type == "memory":
        store = InMemoryIdentityStore()
    else:
        store = SQLiteIdentityStore(settings.identity.sqlite_path)
    return IdentityService(store=store, default_console_person_id=settings.identity.default_console_person_id)


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=f"payload.{key} must be a non-empty string")
    return value.strip()


def _resolution(value: Any) -> str:
    normalized = str(value).strip()
    if normalized not in {"unknown", "probable", "verified"}:
        raise HTTPException(status_code=400, detail="payload.resolution must be unknown/probable/verified")
    return normalized


def _binding_evidence(value: Any) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="payload.binding_evidence must be a list")
    return [dict(item) for item in value if isinstance(item, dict)]


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "service.toml"
app = create_app(load_service_config(DEFAULT_CONFIG_PATH))
