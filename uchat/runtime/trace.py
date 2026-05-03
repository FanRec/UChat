from __future__ import annotations

import json
import threading
from typing import Any, Protocol

from uchat.contracts import NormalizedEvent, OutboundEvent, SessionState
from uchat.debug import DebugWriter
from uchat.models import LLMStreamEvent


class RuntimeObserver(Protocol):
    def start_turn(self, message_chain: dict[str, Any]) -> None: ...

    def record_stage(self, message_chain: dict[str, Any], stage: dict[str, Any]) -> None: ...

    def record_llm_event(self, trace_id: str, event: LLMStreamEvent, event_index: int) -> None: ...

    def finish_turn(self, message_chain: dict[str, Any], outbound: OutboundEvent) -> None: ...


class MessageChainRecorder:
    def __init__(
        self,
        *,
        state: SessionState,
        debug_writer: DebugWriter,
        observer: RuntimeObserver | None = None,
    ) -> None:
        self.state = state
        self.debug_writer = debug_writer
        self.observer = observer
        self._lock = threading.RLock()

    def start_turn(self, event: NormalizedEvent) -> dict[str, Any]:
        message_chain = {
            "trace_id": event.trace_id,
            "event_id": event.event_id,
            "session_id": event.session_window_id,
            "scene_id": event.scene_id,
            "user_input": event.content_norm,
            "scene_state": self.state.scene_state.to_dict(),
            "session_state": self.state.session_snapshot.to_dict(),
            "short_history_before": list(self.state.short_history),
            "degraded": False,
            "degraded_reason": "",
            "short_history_after": list(self.state.short_history),
            "stages": [],
        }
        if self.observer is not None:
            self.observer.start_turn(message_chain)
        return message_chain

    def finish_turn(self, message_chain: dict[str, Any], outbound: OutboundEvent) -> None:
        with self._lock:
            if self.observer is not None:
                self.observer.finish_turn(message_chain, outbound)

    def record_llm_event(self, trace_id: str, event: LLMStreamEvent, event_index: int) -> None:
        with self._lock:
            if self.observer is not None:
                self.observer.record_llm_event(trace_id, event, event_index)

    def record_stage(
        self,
        trace_id: str,
        message_chain: dict[str, Any],
        *,
        queue_snapshot: dict[str, int] | None = None,
        stage: str,
        service: str,
        status: str,
        latency_ms: float | None = None,
        decision: str | None = None,
        fallback_source: str | None = None,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            entry: dict[str, Any] = {
                "index": len(message_chain["stages"]) + 1,
                "stage": stage,
                "service": service,
                "status": status,
                "decision": decision,
                "latency_ms": latency_ms,
                "fallback_source": fallback_source,
                "degraded": self.state.degraded,
                "error": error,
                "metrics": metrics or {},
                "result": result or self.result(),
            }
            message_chain["degraded"] = self.state.degraded
            message_chain["degraded_reason"] = self.state.degraded_reason
            message_chain["short_history_after"] = list(self.state.short_history)
            message_chain["scene_state"] = self.state.scene_state.to_dict()
            session_state = self.state.session_snapshot.to_dict()
            if queue_snapshot is not None:
                session_state["current_output_occupancy"] = queue_snapshot["depth"] + queue_snapshot["in_flight"]
            message_chain["session_state"] = session_state
            message_chain["stages"].append(entry)
            self.debug_writer.write(trace_id, "message_chain.json", message_chain)
            if self.observer is not None:
                self.observer.record_stage(message_chain, entry)

    @staticmethod
    def result(
        *,
        kind: str = "none",
        summary: str | None = None,
        full: str | None = None,
        mime: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "summary": summary,
            "full": full,
            "mime": mime,
        }

    @staticmethod
    def json_text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
