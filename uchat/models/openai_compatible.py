from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal

import httpx

from uchat.models.router import ModelProfileConfig, ModelProviderConfig


class LLMError(RuntimeError):
    pass


LLMStreamEventType = Literal[
    "response_started",
    "text_delta",
    "sentence",
    "response_completed",
    "response_error",
]


@dataclass(frozen=True)
class LLMStreamEvent:
    event_type: LLMStreamEventType
    text: str = ""
    delta: str = ""
    created_at_ms: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    text: str
    request: dict[str, Any]
    response: dict[str, Any]
    latency_ms: float


@dataclass(frozen=True)
class LLMStreamAggregate:
    text: str
    request: dict[str, Any]
    response_events: list[LLMStreamEvent]
    latency_ms: float
    first_token_latency_ms: float | None
    first_sentence_latency_ms: float | None
    delta_count: int
    sentence_count: int


class OpenAICompatibleClient:
    def __init__(
        self,
        provider: ModelProviderConfig,
        profile: ModelProfileConfig,
        *,
        client: httpx.Client | None = None,
    ):
        self.provider = provider
        self.profile = profile
        self.client = client or httpx.Client(base_url=provider.base_url, timeout=provider.timeout_seconds, trust_env=False)

    def create_reply(self, *, prompt: str, trace_id: str) -> LLMResult:
        payload = self._payload(prompt=prompt, stream=False)
        headers = self._headers(trace_id)
        started = time.perf_counter()
        try:
            response = self.client.post("/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider.provider_id} request failed: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise LLMError(f"{self.provider.provider_id} returned {response.status_code}: {response.text}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError(f"{self.provider.provider_id} returned non-JSON response") from exc
        text = _extract_text(body)
        if not text:
            raise LLMError(f"{self.provider.provider_id} response did not contain reply text")
        return LLMResult(text=text, request={"body": payload, "headers": headers}, response=body, latency_ms=latency_ms)

    def stream_reply(self, *, prompt: str, trace_id: str) -> Iterator[LLMStreamEvent]:
        payload = self._payload(prompt=prompt, stream=True)
        headers = self._headers(trace_id)
        started = time.perf_counter()
        full_text = ""
        sentence_buffer = ""
        started_event_sent = False

        try:
            with self.client.stream("POST", "/chat/completions", json=payload, headers=headers) as response:
                if response.status_code != 200:
                    raise LLMError(f"{self.provider.provider_id} returned {response.status_code}: {response.text}")
                for raw_line in response.iter_lines():
                    if raw_line is None:
                        continue
                    line = raw_line.strip()
                    if not line:
                        continue
                    if not started_event_sent:
                        started_event_sent = True
                        yield LLMStreamEvent(
                            event_type="response_started",
                            created_at_ms=(time.perf_counter() - started) * 1000,
                        )
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        trailing_sentence = sentence_buffer.strip()
                        if trailing_sentence:
                            yield LLMStreamEvent(
                                event_type="sentence",
                                text=trailing_sentence,
                                created_at_ms=(time.perf_counter() - started) * 1000,
                            )
                        yield LLMStreamEvent(
                            event_type="response_completed",
                            text=full_text,
                            created_at_ms=(time.perf_counter() - started) * 1000,
                        )
                        return
                    try:
                        body = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise LLMError(f"{self.provider.provider_id} stream returned invalid JSON chunk: {data}") from exc
                    delta = _extract_delta_text(body)
                    if not delta:
                        continue
                    full_text += delta
                    sentence_buffer += delta
                    event_time_ms = (time.perf_counter() - started) * 1000
                    yield LLMStreamEvent(
                        event_type="text_delta",
                        delta=delta,
                        text=full_text,
                        created_at_ms=event_time_ms,
                    )
                    completed_sentences, remainder = _take_complete_sentences(sentence_buffer)
                    if completed_sentences:
                        for sentence in completed_sentences:
                            yield LLMStreamEvent(
                                event_type="sentence",
                                text=sentence,
                                created_at_ms=(time.perf_counter() - started) * 1000,
                            )
                        sentence_buffer = remainder
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider.provider_id} streaming request failed: {exc}") from exc
        except LLMError as exc:
            yield LLMStreamEvent(
                event_type="response_error",
                created_at_ms=(time.perf_counter() - started) * 1000,
                detail={"error": str(exc)},
            )
            raise

    def aggregate_stream(
        self,
        *,
        prompt: str,
        trace_id: str,
        on_event: Callable[[LLMStreamEvent], None] | None = None,
    ) -> LLMStreamAggregate:
        payload = self._payload(prompt=prompt, stream=True)
        headers = self._headers(trace_id)
        events: list[LLMStreamEvent] = []
        first_token_latency_ms: float | None = None
        first_sentence_latency_ms: float | None = None
        final_text = ""
        for event in self.stream_reply(prompt=prompt, trace_id=trace_id):
            events.append(event)
            if on_event is not None:
                on_event(event)
            if event.event_type == "text_delta" and first_token_latency_ms is None:
                first_token_latency_ms = round(event.created_at_ms, 2)
            if event.event_type == "sentence" and first_sentence_latency_ms is None:
                first_sentence_latency_ms = round(event.created_at_ms, 2)
            if event.event_type == "response_completed":
                final_text = event.text
            elif event.event_type == "text_delta":
                final_text = event.text
            elif event.event_type == "response_error":
                raise LLMError(event.detail.get("error", f"{self.provider.provider_id} stream failed"))
        if not final_text.strip():
            raise LLMError(f"{self.provider.provider_id} streaming response did not contain reply text")
        completed_event = next((event for event in reversed(events) if event.event_type == "response_completed"), None)
        latency_ms = round(completed_event.created_at_ms if completed_event is not None else events[-1].created_at_ms, 2)
        return LLMStreamAggregate(
            text=final_text.strip(),
            request={"body": payload, "headers": headers},
            response_events=events,
            latency_ms=latency_ms,
            first_token_latency_ms=first_token_latency_ms,
            first_sentence_latency_ms=first_sentence_latency_ms,
            delta_count=sum(1 for event in events if event.event_type == "text_delta"),
            sentence_count=sum(1 for event in events if event.event_type == "sentence"),
        )

    def close(self) -> None:
        self.client.close()

    def _payload(self, *, prompt: str, stream: bool) -> dict[str, Any]:
        return {
            "model": self.profile.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.max_tokens,
            "stream": stream,
        }

    def _headers(self, trace_id: str) -> dict[str, str]:
        if not self.provider.api_key:
            raise LLMError(f"provider '{self.provider.provider_id}' is missing API key")
        return {
            "Authorization": f"Bearer {self.provider.api_key}",
            "Content-Type": "application/json",
            "X-Trace-Id": trace_id,
        }


def _extract_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def _extract_delta_text(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _take_complete_sentences(buffer: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    start = 0
    index = 0
    soft_stop_chars = "，,、：:）)】》"
    min_soft_chunk_chars = 12
    enable_soft_split = _experimental_soft_sentence_split_enabled()
    while index < len(buffer):
        char = buffer[index]
        if char in "。！？!?；;.":
            sentence = buffer[start : index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1
        elif enable_soft_split and char in soft_stop_chars:
            candidate = buffer[start : index + 1].strip()
            if len(candidate) >= min_soft_chunk_chars:
                sentences.append(candidate)
                start = index + 1
        elif char == "\n" and index + 1 < len(buffer) and buffer[index + 1] == "\n":
            sentence = buffer[start:index].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 2
            index += 1
        index += 1
    return sentences, buffer[start:]


def _experimental_soft_sentence_split_enabled() -> bool:
    value = os.getenv("UCHAT_EXPERIMENTAL_SOFT_SENTENCE_SPLIT", "").strip().lower()
    return value in {"1", "true", "yes", "on"}
