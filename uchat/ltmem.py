from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from uchat.config import LTMemConfig
from uchat.contracts import NormalizedEvent, new_event_id, utc_now_iso


class LTMemError(RuntimeError):
    pass


@dataclass(frozen=True)
class LTMemCallResult:
    request: dict[str, Any]
    response: dict[str, Any]
    latency_ms: float


class LTMemGateway:
    def __init__(self, config: LTMemConfig, *, client: httpx.Client | None = None):
        self.config = config
        self.client = client or httpx.Client(base_url=config.base_url, timeout=config.timeout_seconds, trust_env=False)

    def check_health(self) -> dict[str, Any]:
        response = self._get_health_with_retry()
        if response.status_code != 200:
            raise LTMemError(f"LTMem health check returned {response.status_code}: {response.text}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LTMemError("LTMem health check returned non-JSON response") from exc
        if payload.get("status") != "ok":
            error = payload.get("error") or payload
            raise LTMemError(f"LTMem health check status is not ok: {error}")
        return payload

    def _get_health_with_retry(self) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        last_response: httpx.Response | None = None
        for attempt in range(5):
            try:
                response = self.client.get("/health")
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 500:
                    return response
                last_response = response
            if attempt < 4:
                time.sleep(0.4)
        if last_response is not None:
            return last_response
        if last_error is not None:
            raise LTMemError(f"LTMem health check failed: /health: {last_error}") from last_error
        raise LTMemError("LTMem health check failed: no response")

    def ingest_event(self, event: NormalizedEvent) -> LTMemCallResult:
        payload = event.to_dict()
        response, latency_ms = self._post("/v1/events:ingest", payload, event.trace_id, {202})
        return LTMemCallResult(request=payload, response=response, latency_ms=latency_ms)

    def ingest_assistant_reply(
        self,
        *,
        content: str,
        scene_id: str,
        session_window_id: str,
        reply_to_trace_id: str,
        trace_id: str,
    ) -> LTMemCallResult:
        payload = {
            "event_id": new_event_id(),
            "trace_id": trace_id,
            "occurred_at": utc_now_iso(),
            "event_kind": "speech",
            "source_type": "assistant_reply",
            "scene_id": scene_id,
            "session_window_id": session_window_id,
            "content_raw": content,
            "content_norm": content,
            "speaker_candidates": [
                {
                    "entity_id": "self",
                    "speaker_role": "self",
                    "confidence": 1.0,
                    "source": "uchat",
                }
            ],
            "binding_evidence": [
                {
                    "evidence_type": "system_identity",
                    "value": "self",
                    "confidence": 1.0,
                }
            ],
            "binding_confidence": 1.0,
            "emotion_context": {},
            "tool_context": {},
            "privacy_level": 1,
            "metadata": {
                "origin_system": "uchat",
                "platform": "console",
                "reply_to_trace_id": reply_to_trace_id,
            },
        }
        response, latency_ms = self._post("/v1/events:ingest", payload, trace_id, {202})
        return LTMemCallResult(request=payload, response=response, latency_ms=latency_ms)

    def build_context_pack(
        self,
        *,
        query_text: str,
        scene_id: str,
        session_window_id: str,
        audience_scope: str,
        trace_id: str,
        speaker_id: str | None = "user_local",
    ) -> LTMemCallResult:
        payload = {
            "query_text": query_text,
            "scene_id": scene_id,
            "speaker_id": speaker_id,
            "session_window_id": session_window_id,
            "audience_scope": audience_scope,
            "include_subjective": False,
        }
        response, latency_ms = self._post("/v1/context-packs:build", payload, trace_id, {200})
        return LTMemCallResult(request=payload, response=response, latency_ms=latency_ms)

    def close(self) -> None:
        self.client.close()

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        trace_id: str,
        expected_statuses: set[int],
    ) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        try:
            response = self.client.post(path, json=payload, headers={"X-Trace-Id": trace_id})
        except httpx.HTTPError as exc:
            raise LTMemError(f"LTMem request failed: {path}: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code not in expected_statuses:
            raise LTMemError(f"LTMem {path} returned {response.status_code}: {response.text}")
        try:
            return response.json(), latency_ms
        except ValueError as exc:
            raise LTMemError(f"LTMem {path} returned non-JSON response") from exc
