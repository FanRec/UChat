from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from uchat.adapters import EmbodimentAdapter, OutputAdapter, TTSAdapter
from uchat.contracts import DeliveryReceipt, OutboundEvent, OutputSegment, OutputTask, SessionState
from uchat.models import LLMStreamEvent
from uchat.output_queue import OutputQueue, OutputQueueError
from uchat.runtime.trace import MessageChainRecorder


def _invoke_cancel(adapter: Any, task: OutputTask) -> DeliveryReceipt | None:
    cancel_method = getattr(adapter, "cancel_task", None)
    if callable(cancel_method):
        return cancel_method(task)
    return None


@dataclass
class _Worker:
    name: str
    channels: set[str]
    destinations: set[str]
    deliver: Callable[[OutputTask, OutboundEvent | None], DeliveryReceipt]
    cancel: Callable[[OutputTask], DeliveryReceipt | None] | None = None

    def matches(self, task: OutputTask) -> bool:
        return task.channel in self.channels and task.destination in self.destinations


class RuntimeOutputManager:
    def __init__(
        self,
        *,
        queue: OutputQueue,
        state: SessionState,
        recorder: MessageChainRecorder,
        settings,
        logger,
        mark_degraded: Callable[[str], None],
        output_adapter: OutputAdapter | None = None,
        tts_adapter: TTSAdapter | None = None,
        embodiment_adapter: EmbodimentAdapter | None = None,
        subtitle_adapter: OutputAdapter | None = None,
    ) -> None:
        self.queue = queue
        self.state = state
        self.recorder = recorder
        self.settings = settings
        self.logger = logger
        self.mark_degraded = mark_degraded
        self.output_adapter = output_adapter
        self.tts_adapter = tts_adapter
        self.embodiment_adapter = embodiment_adapter
        self.subtitle_adapter = subtitle_adapter
        self._queue_lock = threading.RLock()
        self._async_tts = bool(getattr(self.tts_adapter, "supports_background_delivery", False))
        self._tts_wake = threading.Event()
        self._tts_stop = threading.Event()
        self._tts_thread: threading.Thread | None = None
        self._tts_concurrency_limit = 2 if self._async_tts else 1
        self._sentence_index_by_trace: dict[str, int] = {}
        self._generation_id_by_trace: dict[str, int] = {}
        self._next_generation_id_by_trace: dict[str, int] = {}
        self._retry_limits_by_channel = {
            "tts": 1,
        }
        self._workers = [
            _Worker(
                name="tts",
                channels={"tts"},
                destinations={"tts_service", "console"},
                deliver=self._deliver_tts,
                cancel=(lambda task: _invoke_cancel(self.tts_adapter, task)) if self.tts_adapter is not None else None,
            ),
            _Worker(
                name="obs",
                channels={"subtitle", "status"},
                destinations={"obs"},
                deliver=self._deliver_obs,
                cancel=(lambda task: _invoke_cancel(self.subtitle_adapter, task)) if self.subtitle_adapter is not None else None,
            ),
            _Worker(
                name="output",
                channels={"text"},
                destinations={"console", "bilibili"},
                deliver=self._deliver_output,
                cancel=(lambda task: _invoke_cancel(self.output_adapter, task)) if self.output_adapter is not None else None,
            ),
        ]
        if self._async_tts:
            self._ensure_tts_worker()

    def snapshot(self) -> dict[str, int]:
        with self._queue_lock:
            snapshot = self.queue.snapshot()
            snapshot["paused_channels"] = self.queue.paused_channels
            snapshot["paused_traces"] = self.queue.paused_traces
            return snapshot

    def handle_sentence(
        self,
        *,
        trace_id: str,
        text: str,
        message_chain: dict[str, Any],
    ) -> None:
        event_index = self._sentence_index_by_trace.get(trace_id, 0) + 1
        self._sentence_index_by_trace[trace_id] = event_index
        generation_id = self._ensure_generation_id(trace_id)
        segment = OutputSegment.sentence(
            trace_id=trace_id,
            index=event_index,
            text=text,
            metadata={"generation_id": generation_id},
        )
        task = OutputTask.from_segment(
            segment=segment,
            destination="tts_service" if self.tts_adapter is not None else "console",
            channel="tts",
            timeout_ms=self.settings.services.tts.timeout_ms,
        )
        self.enqueue_task(task, message_chain)
        if self._async_tts:
            self.recorder.record_llm_event(
                trace_id,
                LLMStreamEvent(event_type="sentence", text=text, created_at_ms=0.0),
                event_index,
            )
            self._tts_wake.set()
            if self.subtitle_adapter is not None and not self._use_playback_subtitle_sync():
                self._send_sentence_subtitle(trace_id=trace_id, text=text, event_index=event_index)
            return
        self.drain(message_chain)
        if self.tts_adapter is None:
            self.recorder.record_llm_event(
                trace_id,
                LLMStreamEvent(event_type="sentence", text=text, created_at_ms=0.0),
                event_index,
            )
        if self.subtitle_adapter is not None and not self._use_playback_subtitle_sync():
            self._send_sentence_subtitle(trace_id=trace_id, text=text, event_index=event_index)

    def dispatch_final_outbound(self, outbound: OutboundEvent, message_chain: dict[str, Any]) -> None:
        final_tasks = [
            OutputTask(
                task_id=outbound.outbound_id,
                trace_id=outbound.trace_id,
                channel=outbound.channel,
                destination=outbound.destination,
                text=outbound.text,
                priority=60,
                metadata={"outbound_event": outbound.to_dict()},
            )
        ]
        if self.subtitle_adapter is not None and not self._use_playback_subtitle_sync():
            final_tasks.append(
                OutputTask(
                    task_id=f"{outbound.outbound_id}_subtitle_turn_end",
                    trace_id=outbound.trace_id,
                    channel="subtitle",
                    destination="obs",
                    text="",
                    priority=55,
                    metadata={"outbound_event": outbound.to_dict(), "action": "turn_end"},
                )
            )
        final_outbound_by_task = {task.task_id: outbound for task in final_tasks}
        for task in final_tasks:
            self.enqueue_task(task, message_chain)
        if self._use_playback_subtitle_sync() and self.tts_adapter is not None:
            mark_turn_end = getattr(self.tts_adapter, "mark_turn_end", None)
            if callable(mark_turn_end):
                try:
                    mark_turn_end(
                        trace_id=outbound.trace_id,
                        generation_id=self._ensure_generation_id(outbound.trace_id),
                        last_segment_index=self._sentence_index_by_trace.get(outbound.trace_id, 0),
                        timeout_ms=self.settings.services.tts.timeout_ms,
                    )
                except Exception:
                    self.logger.exception(
                        "tts turn_end notification failed",
                        extra={"stage": "output_dispatch", "service": "output_delivery", "status": "error"},
                    )
        if self._async_tts:
            self.drain(message_chain, final_outbound_by_task=final_outbound_by_task, worker_names={"obs", "output"})
            self._tts_wake.set()
        else:
            self.drain(message_chain, final_outbound_by_task=final_outbound_by_task)
        self._sentence_index_by_trace.pop(outbound.trace_id, None)
        self._complete_generation(outbound.trace_id)

    def _use_playback_subtitle_sync(self) -> bool:
        return bool(getattr(self.tts_adapter, "supports_playback_subtitle_sync", False))

    def interrupt_channel(
        self,
        *,
        trace_id: str,
        message_chain: dict[str, Any],
        channel: str,
    ) -> list[OutputTask]:
        if channel == "tts" and self.tts_adapter is not None:
            cancel_trace = getattr(self.tts_adapter, "cancel_trace", None)
            if callable(cancel_trace):
                try:
                    cancel_trace(trace_id, timeout_ms=self.settings.services.tts.timeout_ms)
                except Exception:
                    self.logger.exception(
                        "tts trace cancellation failed",
                        extra={"stage": "queue_cancelled", "service": "output_delivery", "status": "error"},
                    )
        with self._queue_lock:
            cancelled = self.queue.cancel_pending(trace_id=trace_id, channel=channel)
            in_flight = self.queue.in_flight_tasks(trace_id=trace_id, channel=channel)
        for task in in_flight:
            worker = self._worker_for(task)
            if worker is not None and worker.cancel is not None:
                try:
                    worker.cancel(task)
                except Exception:
                    self.logger.exception(
                        "output task cancellation failed",
                        extra={"stage": "queue_cancelled", "service": "output_delivery", "status": "error"},
                    )
        with self._queue_lock:
            cancelled.extend(self.queue.cancel_in_flight(trace_id=trace_id, channel=channel))
        if not cancelled:
            self.state.session_snapshot.last_interrupt_count = 0
            return []

        self.state.session_snapshot.last_interrupt_count = len(cancelled)
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="interrupted",
            service="runtime",
            status="ok",
            metrics={"cancelled_tasks": len(cancelled), "channel": channel},
            result=MessageChainRecorder.result(
                kind="summary",
                summary=f"cancelled {len(cancelled)} pending output task(s)",
            ),
        )
        for task in cancelled:
            self.recorder.record_stage(
                trace_id,
                message_chain,
                queue_snapshot=self.snapshot(),
                stage="queue_cancelled",
                service="output_queue",
                status="cancelled",
                metrics={"channel": task.channel, "destination": task.destination, "segment_index": task.segment_index},
                result=MessageChainRecorder.result(kind="summary", summary=f"cancelled task {task.task_id}"),
            )
        self._update_state_snapshot()
        self._reset_generation(trace_id)
        return cancelled

    def pause_channel(self, *, trace_id: str, message_chain: dict[str, Any], channel: str) -> bool:
        with self._queue_lock:
            changed = self.queue.pause_channel(channel)
        stage_status = "ok" if changed else "unchanged"
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="queue_paused",
            service="output_queue",
            status=stage_status,
            metrics={"channel": channel, "paused_channels": list(self.queue.paused_channels)},
            result=MessageChainRecorder.result(kind="summary", summary=f"paused channel {channel}"),
        )
        self._update_state_snapshot()
        return changed

    def resume_channel(self, *, trace_id: str, message_chain: dict[str, Any], channel: str) -> bool:
        with self._queue_lock:
            changed = self.queue.resume_channel(channel)
        stage_status = "ok" if changed else "unchanged"
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="queue_resumed",
            service="output_queue",
            status=stage_status,
            metrics={"channel": channel, "paused_channels": list(self.queue.paused_channels)},
            result=MessageChainRecorder.result(kind="summary", summary=f"resumed channel {channel}"),
        )
        self._update_state_snapshot()
        if changed and self._async_tts and channel == "tts":
            self._tts_wake.set()
        return changed

    def pause_trace(self, *, trace_id: str, message_chain: dict[str, Any], target_trace_id: str) -> bool:
        with self._queue_lock:
            changed = self.queue.pause_trace(target_trace_id)
        stage_status = "ok" if changed else "unchanged"
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="trace_paused",
            service="output_queue",
            status=stage_status,
            metrics={"target_trace_id": target_trace_id, "paused_traces": list(self.queue.paused_traces)},
            result=MessageChainRecorder.result(kind="summary", summary=f"paused trace {target_trace_id}"),
        )
        self._update_state_snapshot()
        return changed

    def resume_trace(self, *, trace_id: str, message_chain: dict[str, Any], target_trace_id: str) -> bool:
        with self._queue_lock:
            changed = self.queue.resume_trace(target_trace_id)
        stage_status = "ok" if changed else "unchanged"
        self.recorder.record_stage(
            trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="trace_resumed",
            service="output_queue",
            status=stage_status,
            metrics={"target_trace_id": target_trace_id, "paused_traces": list(self.queue.paused_traces)},
            result=MessageChainRecorder.result(kind="summary", summary=f"resumed trace {target_trace_id}"),
        )
        self._update_state_snapshot()
        if changed and self._async_tts:
            self._tts_wake.set()
        return changed

    def enqueue_task(self, task: OutputTask, message_chain: dict[str, Any]) -> None:
        task.metadata["_message_chain_ref"] = message_chain
        try:
            with self._queue_lock:
                queued_task, replaced = self.queue.enqueue(task)
        except OutputQueueError as exc:
            self.recorder.record_stage(
                task.trace_id,
                message_chain,
                queue_snapshot=self.snapshot(),
                stage="queue_enqueued",
                service="output_queue",
                status="error",
                error=str(exc),
                metrics=self.snapshot(),
                result=MessageChainRecorder.result(kind="summary", summary="failed to enqueue output task"),
            )
            raise
        for replaced_task in replaced:
            self.recorder.record_stage(
                task.trace_id,
                message_chain,
                queue_snapshot=self.snapshot(),
                stage="queue_cancelled",
                service="output_queue",
                status="cancelled",
                metrics={"channel": replaced_task.channel, "destination": replaced_task.destination, "segment_index": replaced_task.segment_index},
                result=MessageChainRecorder.result(kind="summary", summary=f"replaced task {replaced_task.task_id}"),
            )
        self.recorder.record_stage(
            task.trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="queue_enqueued",
            service="output_queue",
            status="ok",
            metrics=self.snapshot() | {"channel": queued_task.channel, "destination": queued_task.destination, "priority": queued_task.priority},
            result=MessageChainRecorder.result(kind="summary", summary=f"enqueued task {queued_task.task_id}"),
        )
        self._update_state_snapshot()

    def drain(
        self,
        message_chain: dict[str, Any],
        *,
        final_outbound_by_task: dict[str, OutboundEvent] | None = None,
        worker_names: set[str] | None = None,
    ) -> None:
        final_outbound_by_task = final_outbound_by_task or {}
        while True:
            progressed = False
            for worker in self._workers:
                if worker_names is not None and worker.name not in worker_names:
                    continue
                with self._queue_lock:
                    task = self.queue.pop_next(channels=worker.channels, destinations=worker.destinations)
                if task is None:
                    continue
                progressed = True
                with self._queue_lock:
                    self.queue.mark_started(task)
                queue_wait_ms = None
                if task.queued_at_ms is not None and task.started_at_ms is not None:
                    queue_wait_ms = round(task.started_at_ms - task.queued_at_ms, 2)
                self._process_task(
                    worker=worker,
                    task=task,
                    message_chain=message_chain,
                    queue_wait_ms=queue_wait_ms,
                    final_outbound=final_outbound_by_task.get(task.task_id),
                )
            if not progressed:
                self._update_state_snapshot()
                return

    def _update_state_snapshot(self) -> None:
        snapshot = self.snapshot()
        self.state.active_output_count = snapshot["depth"] + snapshot["in_flight"]
        self.state.session_snapshot.current_output_occupancy = self.state.active_output_count

    def _ensure_generation_id(self, trace_id: str) -> int:
        current = self._generation_id_by_trace.get(trace_id)
        if current is not None:
            return current
        next_id = self._next_generation_id_by_trace.get(trace_id, 1)
        self._generation_id_by_trace[trace_id] = next_id
        self._next_generation_id_by_trace[trace_id] = next_id
        return next_id

    def _complete_generation(self, trace_id: str) -> None:
        current = self._generation_id_by_trace.pop(trace_id, None)
        if current is None:
            return
        self._next_generation_id_by_trace[trace_id] = current + 1

    def _reset_generation(self, trace_id: str) -> None:
        current = self._generation_id_by_trace.pop(trace_id, None)
        if current is not None:
            self._next_generation_id_by_trace[trace_id] = current + 1

    def _send_sentence_subtitle(self, *, trace_id: str, text: str, event_index: int) -> None:
        """在后台线程发送逐句字幕，不阻塞 LLM 流。"""
        outbound = OutboundEvent(
            outbound_id=f"{trace_id}_sub_{event_index}",
            trace_id=trace_id,
            channel="subtitle",
            destination="obs",
            text=text,
            metadata={"action": "sentence", "sentence_index": event_index},
        )

        def _fire() -> None:
            try:
                if self.subtitle_adapter is not None:
                    self.subtitle_adapter.send(outbound)
            except Exception:
                pass  # 字幕是展示层，失败不影响核心链路

        threading.Thread(target=_fire, daemon=True).start()

    def _ensure_tts_worker(self) -> None:
        if not self._async_tts:
            return
        if self._tts_thread is not None and self._tts_thread.is_alive():
            return
        self._tts_thread = threading.Thread(target=self._run_tts_worker, name="uchat-tts-worker", daemon=True)
        self._tts_thread.start()

    def _run_tts_worker(self) -> None:
        worker = next((item for item in self._workers if item.name == "tts"), None)
        if worker is None:
            return
        while not self._tts_stop.is_set():
            self._tts_wake.wait(timeout=0.2)
            self._tts_wake.clear()
            while not self._tts_stop.is_set():
                launched = 0
                while launched < self._tts_concurrency_limit and not self._tts_stop.is_set():
                    with self._queue_lock:
                        task = self.queue.pop_next(channels=worker.channels, destinations={"tts_service"})
                    if task is None:
                        break
                    with self._queue_lock:
                        self.queue.mark_started(task)
                    queue_wait_ms = None
                    if task.queued_at_ms is not None and task.started_at_ms is not None:
                        queue_wait_ms = round(task.started_at_ms - task.queued_at_ms, 2)
                    message_chain = task.metadata.get("_message_chain_ref")
                    if not isinstance(message_chain, dict):
                        continue
                    threading.Thread(
                        target=self._process_task,
                        kwargs={
                            "worker": worker,
                            "task": task,
                            "message_chain": message_chain,
                            "queue_wait_ms": queue_wait_ms,
                            "final_outbound": None,
                        },
                        daemon=True,
                    ).start()
                    launched += 1
                if launched == 0:
                    break
                time.sleep(0.02)
            self._update_state_snapshot()

    def _process_task(
        self,
        *,
        worker: _Worker,
        task: OutputTask,
        message_chain: dict[str, Any],
        queue_wait_ms: float | None,
        final_outbound: OutboundEvent | None,
    ) -> None:
        self.recorder.record_stage(
            task.trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="delivery_started",
            service="output_delivery",
            status="started",
            metrics={
                "channel": task.channel,
                "destination": task.destination,
                "worker": worker.name,
                "queue_wait_ms": queue_wait_ms,
            },
            result=MessageChainRecorder.result(kind="summary", summary=f"delivery started for {task.task_id}"),
        )
        try:
            receipt = worker.deliver(task, final_outbound)
        except Exception as exc:
            with self._queue_lock:
                failed_task = self.queue.mark_failed(task, str(exc))
            if self._should_retry(task=failed_task):
                requeued_task = self._requeue_for_retry(
                    failed_task,
                    error=str(exc),
                    message_chain=message_chain,
                    queue_wait_ms=queue_wait_ms,
                )
                if requeued_task.channel == "tts":
                    self.mark_degraded(f"TTS delivery failed and requeued: {exc}")
                    self._tts_wake.set()
                return
            if failed_task.channel == "tts":
                self.mark_degraded(f"TTS delivery failed: {exc}")
            self.recorder.record_stage(
                task.trace_id,
                message_chain,
                queue_snapshot=self.snapshot(),
                stage="delivery_completed",
                service="output_delivery",
                status="failed",
                error=str(exc),
                metrics={
                    "channel": failed_task.channel,
                    "destination": failed_task.destination,
                    "worker": worker.name,
                    "queue_wait_ms": queue_wait_ms,
                    "retry_count": int(failed_task.metadata.get("retry_count", 0) or 0),
                },
                result=MessageChainRecorder.result(kind="summary", summary=f"delivery failed for {failed_task.task_id}"),
            )
            if failed_task.channel != "tts":
                raise
            return
        if receipt.status == "failed" and self._should_retry(task=task):
            requeued_task = self._requeue_for_retry(
                task,
                error=str(receipt.detail.get("reason") or receipt.detail.get("error") or "delivery receipt failed"),
                message_chain=message_chain,
                queue_wait_ms=queue_wait_ms,
            )
            if requeued_task.channel == "tts":
                self.mark_degraded("TTS delivery returned failed receipt and requeued")
                self._tts_wake.set()
            return
        if task.status == "cancelled":
            self.recorder.record_stage(
                task.trace_id,
                message_chain,
                queue_snapshot=self.snapshot(),
                stage="delivery_completed",
                service="output_delivery",
                status="cancelled",
                latency_ms=receipt.latency_ms,
                metrics={
                    "channel": task.channel,
                    "destination": task.destination,
                    "worker": worker.name,
                    "queue_wait_ms": queue_wait_ms,
                    "retry_count": int(task.metadata.get("retry_count", 0) or 0),
                },
                result=MessageChainRecorder.result(
                    kind="json",
                    summary=f"delivery already cancelled for {task.task_id}",
                    full=json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
                    mime="application/json",
                ),
            )
            return
        self._apply_receipt_status(task, receipt)
        if receipt.status == "failed" and task.channel == "tts":
            self.mark_degraded("TTS delivery returned failed receipt")
        self.recorder.record_stage(
            task.trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="delivery_completed",
            service="output_delivery",
            status=receipt.status,
            latency_ms=receipt.latency_ms,
            metrics={
                "channel": task.channel,
                "destination": task.destination,
                "worker": worker.name,
                "queue_wait_ms": queue_wait_ms,
                "retry_count": int(task.metadata.get("retry_count", 0) or 0),
            },
            result=MessageChainRecorder.result(
                kind="json",
                summary=f"delivery completed for {task.task_id}",
                full=json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
                mime="application/json",
            ),
        )

    def _worker_for(self, task: OutputTask) -> _Worker | None:
        for worker in self._workers:
            if worker.matches(task):
                return worker
        return None

    def _deliver_tts(self, task: OutputTask, _: OutboundEvent | None) -> DeliveryReceipt:
        segment_payload = task.metadata.get("segment") or {}
        segment = OutputSegment(
            segment_id=str(segment_payload.get("segment_id", task.task_id)),
            trace_id=task.trace_id,
            index=int(segment_payload.get("index", task.segment_index or 0)),
            text=task.text,
            kind=str(segment_payload.get("kind", "sentence")),
            created_at=str(segment_payload.get("created_at") or task.created_at),
            metadata=dict(segment_payload.get("metadata") or {}),
        )
        if self.tts_adapter is not None:
            return self.tts_adapter.speak(segment, timeout_ms=task.timeout_ms)
        if self.output_adapter is not None:
            return self.output_adapter.send(
                OutboundEvent(
                    outbound_id=task.task_id,
                    trace_id=task.trace_id,
                    channel="tts",
                    destination="console",
                    text=task.text,
                    metadata={"segment_index": segment.index, "task_id": task.task_id},
                )
            )
        return DeliveryReceipt.skipped(
            trace_id=task.trace_id,
            task_id=task.task_id,
            channel=task.channel,
            destination=task.destination,
            detail={"skipped": True},
        )

    def _deliver_obs(self, task: OutputTask, final_outbound: OutboundEvent | None) -> DeliveryReceipt:
        if self.subtitle_adapter is None:
            return DeliveryReceipt.skipped(
                trace_id=task.trace_id,
                task_id=task.task_id,
                channel=task.channel,
                destination=task.destination,
                detail={"skipped": True},
            )
        base = final_outbound or OutboundEvent(
            outbound_id=task.task_id,
            trace_id=task.trace_id,
            channel=task.channel,
            destination=task.destination,
            text=task.text,
            metadata={},
        )
        outbound = OutboundEvent(
            outbound_id=task.task_id,
            trace_id=base.trace_id,
            channel=task.channel,
            destination=task.destination,
            text=task.text,
            metadata=dict(base.metadata),
        )
        outbound.metadata["task_id"] = task.task_id
        if "action" in task.metadata:
            outbound.metadata["action"] = task.metadata["action"]
        return self.subtitle_adapter.send(outbound)

    def _deliver_output(self, task: OutputTask, final_outbound: OutboundEvent | None) -> DeliveryReceipt:
        outbound = final_outbound
        if outbound is None:
            outbound = OutboundEvent(
                outbound_id=task.task_id,
                trace_id=task.trace_id,
                channel=task.channel,
                destination=task.destination,
                text=task.text,
                metadata={"task_id": task.task_id},
            )
        outbound.metadata["task_id"] = task.task_id
        if self.output_adapter is not None:
            return self.output_adapter.send(outbound)
        return DeliveryReceipt.skipped(
            trace_id=task.trace_id,
            task_id=task.task_id,
            channel=task.channel,
            destination=task.destination,
            detail={"skipped": True},
        )

    def _should_retry(self, *, task: OutputTask) -> bool:
        limit = int(self._retry_limits_by_channel.get(task.channel, 0) or 0)
        retry_count = int(task.metadata.get("retry_count", 0) or 0)
        return limit > 0 and retry_count < limit

    def _requeue_for_retry(
        self,
        task: OutputTask,
        *,
        error: str,
        message_chain: dict[str, Any],
        queue_wait_ms: float | None,
    ) -> OutputTask:
        retry_count = int(task.metadata.get("retry_count", 0) or 0) + 1
        task.metadata["retry_count"] = retry_count
        task.metadata["last_error"] = error
        task.metadata["last_failed_at_ms"] = task.completed_at_ms
        task.status = "pending"
        task.started_at_ms = None
        task.completed_at_ms = None
        task.metadata["error"] = error
        with self._queue_lock:
            queued_task, _ = self.queue.enqueue(task)
        self.recorder.record_stage(
            task.trace_id,
            message_chain,
            queue_snapshot=self.snapshot(),
            stage="delivery_requeued",
            service="output_queue",
            status="requeued",
            metrics={
                "channel": queued_task.channel,
                "destination": queued_task.destination,
                "retry_count": retry_count,
                "queue_wait_ms": queue_wait_ms,
            },
            error=error,
            result=MessageChainRecorder.result(kind="summary", summary=f"requeued task {queued_task.task_id} for retry"),
        )
        self._update_state_snapshot()
        return queued_task

    def _apply_receipt_status(self, task: OutputTask, receipt: DeliveryReceipt) -> OutputTask:
        if receipt.status == "delivered":
            with self._queue_lock:
                return self.queue.mark_completed(task)
        if receipt.status == "failed":
            detail = dict(receipt.detail or {})
            error = str(detail.get("reason") or detail.get("error") or "delivery receipt failed")
            with self._queue_lock:
                return self.queue.mark_failed(task, error)
        if receipt.status == "cancelled":
            with self._queue_lock:
                cancelled = self.queue.cancel_in_flight(trace_id=task.trace_id, channel=task.channel, destination=task.destination)
            return cancelled[0] if cancelled else task
        if receipt.status == "skipped":
            task.status = "cancelled"
            task.completed_at_ms = round(time.perf_counter() * 1000, 2)
            task.metadata["skipped"] = True
            with self._queue_lock:
                self.queue._in_flight.pop(task.task_id, None)
                self.queue._remember_history(task)
            return task
        with self._queue_lock:
            return self.queue.mark_completed(task)
