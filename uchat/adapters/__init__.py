from __future__ import annotations

import time
from typing import Any, Callable, Protocol

import httpx
from rich.console import Console

from uchat.config import DebugConfig
from uchat.console_view import MessageChainConsoleRenderer
from uchat.contracts import DeliveryReceipt, EmbodimentCommand, NormalizedEvent, OutboundEvent, OutputSegment, OutputTask
from uchat.models import LLMStreamEvent


class InputAdapter(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def set_event_sink(self, sink: Callable[[NormalizedEvent], None] | None) -> None: ...


class OutputAdapter(Protocol):
    def send(self, event: OutboundEvent) -> DeliveryReceipt: ...


class TTSAdapter(Protocol):
    def speak(self, segment: OutputSegment, *, timeout_ms: int) -> DeliveryReceipt: ...

    @property
    def supports_background_delivery(self) -> bool: ...

    @property
    def supports_playback_subtitle_sync(self) -> bool: ...

    def cancel_trace(self, trace_id: str, *, timeout_ms: int) -> DeliveryReceipt: ...


class EmbodimentAdapter(Protocol):
    def apply_expression(self, command: EmbodimentCommand) -> DeliveryReceipt: ...

    def apply_motion(self, command: EmbodimentCommand) -> DeliveryReceipt: ...

    def sync_audio(self, command: EmbodimentCommand) -> DeliveryReceipt: ...


class ConsoleInputAdapter(InputAdapter):
    def __init__(
        self,
        *,
        scene_id: str = "scene_console_001",
        session_window_id: str = "session_console_001",
    ) -> None:
        self.scene_id = scene_id
        self.session_window_id = session_window_id
        self._sink: Callable[[NormalizedEvent], None] | None = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def set_event_sink(self, sink: Callable[[NormalizedEvent], None] | None) -> None:
        self._sink = sink

    def read_text(self) -> str:
        return input("> ")

    def read(self) -> NormalizedEvent:
        event = NormalizedEvent.from_console(
            text=self.read_text(),
            scene_id=self.scene_id,
            session_window_id=self.session_window_id,
        )
        if self._sink is not None:
            self._sink(event)
        return event


class ConsoleOutputAdapter(OutputAdapter):
    def __init__(self, config: DebugConfig, *, console: Console | None = None):
        self.config = config
        self.console = console or Console()
        self.renderer = MessageChainConsoleRenderer(config, console=self.console)
        self._active_trace_id: str | None = None

    def start_turn(self, message_chain: dict[str, object]) -> None:
        if self.config.console_view not in {"timeline", "conversation"}:
            return
        self._active_trace_id = str(message_chain.get("trace_id", ""))
        self.renderer.render_turn_start(message_chain)

    def record_stage(self, message_chain: dict[str, object], stage: dict[str, object]) -> None:
        if self.config.console_view not in {"timeline", "conversation"}:
            return
        self.renderer.render_stage(stage, live=True)

    def record_llm_event(self, trace_id: str, event: LLMStreamEvent, event_index: int) -> None:
        if self.config.console_view not in {"timeline", "conversation"}:
            return
        if self._active_trace_id != trace_id:
            return
        self.renderer.render_llm_stream_event(event, event_index)

    def finish_turn(self, message_chain: dict[str, object], outbound: OutboundEvent) -> None:
        if self.config.console_view == "conversation":
            self.renderer.render_assistant_message(outbound.text)
        if self._active_trace_id == outbound.trace_id:
            self._active_trace_id = None

    def send(self, event: OutboundEvent) -> DeliveryReceipt:
        started = time.perf_counter()
        task_id = str(event.metadata.get("task_id", event.outbound_id))
        if event.channel == "tts":
            segment_index = int(event.metadata.get("segment_index", 0) or 0)
            self.renderer.render_llm_stream_event(
                LLMStreamEvent(event_type="sentence", text=event.text, created_at_ms=0.0),
                segment_index,
            )
            return DeliveryReceipt.delivered(
                trace_id=event.trace_id,
                task_id=task_id,
                channel=event.channel,
                destination=event.destination,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                detail={"destination": event.destination},
            )
        message_chain = event.metadata.get("message_chain")
        if self.config.console_view in {"timeline", "conversation"} and isinstance(message_chain, dict):
            return DeliveryReceipt.delivered(
                trace_id=event.trace_id,
                task_id=task_id,
                channel=event.channel,
                destination=event.destination,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                detail={"suppressed": True, "destination": event.destination},
            )
        self.console.print(event.text)
        return DeliveryReceipt.delivered(
            trace_id=event.trace_id,
            task_id=task_id,
            channel=event.channel,
            destination=event.destination,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail={"destination": event.destination},
        )


class ConsoleTTSAdapter(TTSAdapter):
    def __init__(self, config: DebugConfig, *, console: Console | None = None):
        self.console = console or Console()
        self.renderer = MessageChainConsoleRenderer(config, console=self.console)

    def speak(self, segment: OutputSegment, *, timeout_ms: int) -> DeliveryReceipt:
        started = time.perf_counter()
        self.renderer.render_llm_stream_event(
            LLMStreamEvent(event_type="sentence", text=segment.text, created_at_ms=0.0),
            segment.index,
        )
        return DeliveryReceipt.delivered(
            trace_id=segment.trace_id,
            task_id=segment.segment_id,
            channel="tts",
            destination="console",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail={"timeout_ms": timeout_ms},
        )

    @property
    def supports_background_delivery(self) -> bool:
        return False

    @property
    def supports_playback_subtitle_sync(self) -> bool:
        return False

    def cancel_trace(self, trace_id: str, *, timeout_ms: int) -> DeliveryReceipt:
        return DeliveryReceipt.cancelled(
            trace_id=trace_id,
            task_id=f"{trace_id}_console_cancel",
            channel="tts",
            destination="console",
            detail={"timeout_ms": timeout_ms},
        )


class ServiceTTSAdapter(TTSAdapter):
    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(base_url=self.base_url, trust_env=False)
        self._subtitle_sync_enabled: bool | None = None
        self._subtitle_sync_checked_at_ms: float = 0.0

    def speak(self, segment: OutputSegment, *, timeout_ms: int) -> DeliveryReceipt:
        started = time.perf_counter()
        response = self.client.post(
            "/v1/tts/speak",
            json={
                "trace_id": segment.trace_id,
                "segment_id": segment.segment_id,
                "index": segment.index,
                "text": segment.text,
                "kind": segment.kind,
                "timeout_ms": timeout_ms,
                "generation_id": segment.metadata.get("generation_id"),
                "metadata": segment.metadata,
            },
            timeout=max(timeout_ms / 1000, 0.1),
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(f"TTS service returned {response.status_code}: {response.text}")
        body = response.json() if response.content else {}
        if body.get("status") != "delivered":
            raise RuntimeError(f"TTS service did not deliver audio: {body}")
        return DeliveryReceipt.delivered(
            trace_id=segment.trace_id,
            task_id=segment.segment_id,
            channel="tts",
            destination="tts_service",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail={
                "timeout_ms": timeout_ms,
                "service_status": body.get("status", "accepted"),
                "audio_id": body.get("audio_id"),
                "duration_ms": body.get("duration_ms"),
                "sample_rate": body.get("sample_rate"),
                "media_type": body.get("media_type"),
                "first_chunk_latency_ms": body.get("first_chunk_latency_ms"),
                "chunk_count": body.get("chunk_count"),
                "streaming_mode": body.get("streaming_mode"),
            },
            metadata={
                "cancel_token": body.get("cancel_token"),
                "audio_path": body.get("audio_path"),
                "playback_started": body.get("playback_started"),
                "playback_completed": body.get("playback_completed"),
                "viseme_stream_id": body.get("viseme_stream_id"),
            },
        )

    @property
    def supports_background_delivery(self) -> bool:
        return True

    @property
    def supports_playback_subtitle_sync(self) -> bool:
        now_ms = time.perf_counter() * 1000
        should_refresh = self._subtitle_sync_enabled is None
        if self._subtitle_sync_enabled is False and now_ms - self._subtitle_sync_checked_at_ms >= 1000:
            should_refresh = True
        if should_refresh:
            self._subtitle_sync_checked_at_ms = now_ms
            try:
                response = self.client.get("/health", timeout=1.0)
            except Exception:
                if self._subtitle_sync_enabled is None:
                    self._subtitle_sync_enabled = True
            else:
                if response.status_code != 200:
                    if self._subtitle_sync_enabled is None:
                        self._subtitle_sync_enabled = True
                else:
                    body = response.json() if response.content else {}
                    self._subtitle_sync_enabled = bool(body.get("subtitle_sync_enabled", False))
        return self._subtitle_sync_enabled

    def cancel_trace(self, trace_id: str, *, timeout_ms: int) -> DeliveryReceipt:
        response = self.client.post(
            "/v1/tts/cancel-trace",
            json={"trace_id": trace_id},
            timeout=max(timeout_ms / 1000, 0.1),
        )
        if response.status_code not in {200, 202}:
            return DeliveryReceipt.failed(
                trace_id=trace_id,
                task_id=f"{trace_id}_cancel_trace",
                channel="tts",
                destination="tts_service",
                detail={"service_status": response.status_code},
            )
        body = response.json() if response.content else {}
        return DeliveryReceipt.cancelled(
            trace_id=trace_id,
            task_id=f"{trace_id}_cancel_trace",
            channel="tts",
            destination="tts_service",
            detail={
                "service_status": body.get("status", "cancelled"),
                "cancelled_task_count": body.get("cancelled_task_count", 0),
            },
        )

    def mark_turn_end(self, *, trace_id: str, generation_id: int, last_segment_index: int, timeout_ms: int) -> DeliveryReceipt:
        response = self.client.post(
            "/v1/tts/turn-end",
            json={
                "trace_id": trace_id,
                "generation_id": generation_id,
                "last_segment_index": last_segment_index,
            },
            timeout=max(timeout_ms / 1000, 0.1),
        )
        if response.status_code not in {200, 202}:
            return DeliveryReceipt.failed(
                trace_id=trace_id,
                task_id=f"{trace_id}_turn_end",
                channel="tts",
                destination="tts_service",
                detail={"service_status": response.status_code},
            )
        return DeliveryReceipt.delivered(
            trace_id=trace_id,
            task_id=f"{trace_id}_turn_end",
            channel="tts",
            destination="tts_service",
            detail={"service_status": "accepted"},
        )

    def cancel_task(self, task: OutputTask) -> DeliveryReceipt:
        response = self.client.post(
            "/v1/tts/cancel",
            json={"task_id": task.task_id, "trace_id": task.trace_id},
            timeout=max(task.timeout_ms / 1000, 0.1),
        )
        if response.status_code not in {200, 202}:
            return DeliveryReceipt.failed(
                trace_id=task.trace_id,
                task_id=task.task_id,
                channel=task.channel,
                destination=task.destination,
                detail={"service_status": response.status_code},
            )
        body = response.json() if response.content else {}
        status = str(body.get("status", "cancelled"))
        if status == "not_found":
            return DeliveryReceipt.failed(
                trace_id=task.trace_id,
                task_id=task.task_id,
                channel=task.channel,
                destination=task.destination,
                detail={"service_status": status},
            )
        return DeliveryReceipt.cancelled(
            trace_id=task.trace_id,
            task_id=task.task_id,
            channel=task.channel,
            destination=task.destination,
            detail={"service_status": status},
            metadata={"cancel_token": body.get("cancel_token")},
        )

    def close(self) -> None:
        self.client.close()


class OBSOutputAdapter(OutputAdapter):
    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(base_url=self.base_url, trust_env=False)

    def send(self, event: OutboundEvent) -> DeliveryReceipt:
        started = time.perf_counter()
        endpoint = "/v1/obs/events"
        if event.channel == "subtitle":
            endpoint = "/v1/obs/subtitle"
        elif event.channel == "status":
            endpoint = "/v1/obs/status"
        response = self.client.post(
            endpoint,
            json={
                "trace_id": event.trace_id,
                "outbound_id": event.outbound_id,
                "channel": event.channel,
                "destination": event.destination,
                "text": event.text,
                "action": event.metadata.get("action", "sentence"),
                "metadata": event.metadata,
            },
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(f"OBS service returned {response.status_code}: {response.text}")
        body = response.json() if response.content else {}
        return DeliveryReceipt.delivered(
            trace_id=event.trace_id,
            task_id=str(event.metadata.get("task_id", event.outbound_id)),
            channel=event.channel,
            destination=event.destination,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail={"service_status": body.get("status", "accepted")},
            metadata={"obs_event_id": body.get("event_id")},
        )

    def cancel_task(self, task: OutputTask) -> DeliveryReceipt:
        response = self.client.post("/v1/obs/cancel", json={"task_id": task.task_id, "trace_id": task.trace_id})
        if response.status_code not in {200, 202}:
            return DeliveryReceipt.failed(
                trace_id=task.trace_id,
                task_id=task.task_id,
                channel=task.channel,
                destination=task.destination,
                detail={"service_status": response.status_code},
            )
        return DeliveryReceipt.cancelled(
            trace_id=task.trace_id,
            task_id=task.task_id,
            channel=task.channel,
            destination=task.destination,
            detail={"service_status": response.status_code},
        )

    def close(self) -> None:
        self.client.close()


class NullEmbodimentAdapter(EmbodimentAdapter):
    def apply_expression(self, command: EmbodimentCommand) -> DeliveryReceipt:
        return DeliveryReceipt.delivered(
            trace_id=command.trace_id,
            task_id=command.command_id,
            channel="body",
            destination="body_service",
        )

    def apply_motion(self, command: EmbodimentCommand) -> DeliveryReceipt:
        return DeliveryReceipt.delivered(
            trace_id=command.trace_id,
            task_id=command.command_id,
            channel="body",
            destination="body_service",
        )

    def sync_audio(self, command: EmbodimentCommand) -> DeliveryReceipt:
        return DeliveryReceipt.delivered(
            trace_id=command.trace_id,
            task_id=command.command_id,
            channel="body",
            destination="body_service",
        )


from uchat.adapters.platform import BilibiliLiveInputAdapter, BilibiliOutputAdapter

__all__ = [
    "BilibiliLiveInputAdapter",
    "BilibiliOutputAdapter",
    "ConsoleInputAdapter",
    "ConsoleOutputAdapter",
    "ConsoleTTSAdapter",
    "EmbodimentAdapter",
    "InputAdapter",
    "NullEmbodimentAdapter",
    "OBSOutputAdapter",
    "OutputAdapter",
    "ServiceTTSAdapter",
    "TTSAdapter",
]
