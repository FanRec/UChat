from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field

from uchat.contracts import OutputTask


class OutputQueueError(RuntimeError):
    pass


@dataclass(order=True)
class _QueueEntry:
    sort_key: tuple[int, int] = field(init=False, repr=False)
    priority: int
    sequence: int
    task: OutputTask = field(compare=False)

    def __post_init__(self) -> None:
        self.sort_key = (-self.priority, self.sequence)


class OutputQueue:
    def __init__(
        self,
        *,
        max_size: int = 64,
        default_timeout_ms: int = 3000,
        history_retention: int = 256,
    ):
        self.max_size = max_size
        self.default_timeout_ms = default_timeout_ms
        self.history_retention = max(0, history_retention)
        self._heap: list[_QueueEntry] = []
        self._sequence = 0
        self._in_flight: dict[str, OutputTask] = {}
        self._history: dict[str, OutputTask] = {}
        self._history_order: list[str] = []
        self._high_watermark = 0
        self._paused_channels: set[str] = set()
        self._paused_traces: set[str] = set()

    def enqueue(self, task: OutputTask) -> tuple[OutputTask, list[OutputTask]]:
        replaced = self.cancel_pending(trace_id=task.trace_id, replace_key=task.replace_key) if task.replace_key else []
        if self.depth >= self.max_size:
            raise OutputQueueError(f"output queue is full ({self.max_size})")
        self._sequence += 1
        task.timeout_ms = task.timeout_ms or self.default_timeout_ms
        task.status = "pending"
        task.queued_at_ms = round(time.perf_counter() * 1000, 2)
        heapq.heappush(self._heap, _QueueEntry(priority=task.priority, sequence=self._sequence, task=task))
        self._remember_history(task)
        self._high_watermark = max(self._high_watermark, self.depth)
        return task, replaced

    def pop_next(self, *, channels: set[str] | None = None, destinations: set[str] | None = None) -> OutputTask | None:
        return self.pop_next_for_targets(channels=channels, destinations=destinations)

    def pop_next_for_channel(self, channel: str) -> OutputTask | None:
        return self.pop_next_for_targets(channels={channel})

    def pop_next_for_targets(
        self,
        *,
        channels: set[str] | None = None,
        destinations: set[str] | None = None,
    ) -> OutputTask | None:
        skipped: list[_QueueEntry] = []
        while self._heap:
            entry = heapq.heappop(self._heap)
            task = entry.task
            if task.status != "pending":
                continue
            if channels is not None and task.channel not in channels:
                skipped.append(entry)
                continue
            if task.channel in self._paused_channels:
                skipped.append(entry)
                continue
            if task.trace_id in self._paused_traces:
                skipped.append(entry)
                continue
            if destinations is not None and task.destination not in destinations:
                skipped.append(entry)
                continue
            now_ms = round(time.perf_counter() * 1000, 2)
            if task.queued_at_ms is not None and task.timeout_ms > 0 and now_ms - task.queued_at_ms > task.timeout_ms:
                task.status = "failed"
                task.completed_at_ms = now_ms
                task.metadata["error"] = "timeout"
                self._remember_history(task)
                continue
            for pending_entry in skipped:
                heapq.heappush(self._heap, pending_entry)
            return task
        for pending_entry in skipped:
            heapq.heappush(self._heap, pending_entry)
        return None

    def mark_started(self, task: OutputTask) -> None:
        task.status = "in_progress"
        task.started_at_ms = round(time.perf_counter() * 1000, 2)
        self._in_flight[task.task_id] = task

    def mark_completed(self, task: OutputTask) -> OutputTask:
        task.status = "completed"
        task.completed_at_ms = round(time.perf_counter() * 1000, 2)
        self._in_flight.pop(task.task_id, None)
        self._remember_history(task)
        return task

    def mark_failed(self, task: OutputTask, error: str) -> OutputTask:
        task.status = "failed"
        task.completed_at_ms = round(time.perf_counter() * 1000, 2)
        task.metadata["error"] = error
        self._in_flight.pop(task.task_id, None)
        self._remember_history(task)
        return task

    def cancel_pending(
        self,
        *,
        trace_id: str | None = None,
        replace_key: str | None = None,
        channel: str | None = None,
        destination: str | None = None,
    ) -> list[OutputTask]:
        cancelled: list[OutputTask] = []
        for entry in self._heap:
            task = entry.task
            if task.status != "pending":
                continue
            if trace_id is not None and task.trace_id != trace_id:
                continue
            if replace_key is not None and task.replace_key != replace_key:
                continue
            if channel is not None and task.channel != channel:
                continue
            if destination is not None and task.destination != destination:
                continue
            task.status = "cancelled"
            task.completed_at_ms = round(time.perf_counter() * 1000, 2)
            self._remember_history(task)
            cancelled.append(task)
        return cancelled

    def cancel_in_flight(
        self,
        *,
        trace_id: str | None = None,
        channel: str | None = None,
        destination: str | None = None,
    ) -> list[OutputTask]:
        cancelled: list[OutputTask] = []
        for task in list(self._in_flight.values()):
            if trace_id is not None and task.trace_id != trace_id:
                continue
            if channel is not None and task.channel != channel:
                continue
            if destination is not None and task.destination != destination:
                continue
            task.status = "cancelled"
            task.completed_at_ms = round(time.perf_counter() * 1000, 2)
            self._in_flight.pop(task.task_id, None)
            self._remember_history(task)
            cancelled.append(task)
        return cancelled

    def in_flight_tasks(
        self,
        *,
        trace_id: str | None = None,
        channel: str | None = None,
        destination: str | None = None,
    ) -> list[OutputTask]:
        tasks: list[OutputTask] = []
        for task in self._in_flight.values():
            if trace_id is not None and task.trace_id != trace_id:
                continue
            if channel is not None and task.channel != channel:
                continue
            if destination is not None and task.destination != destination:
                continue
            tasks.append(task)
        return tasks

    @property
    def depth(self) -> int:
        return sum(1 for entry in self._heap if entry.task.status == "pending")

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    @property
    def high_watermark(self) -> int:
        return self._high_watermark

    @property
    def paused_channels(self) -> tuple[str, ...]:
        return tuple(sorted(self._paused_channels))

    @property
    def paused_traces(self) -> tuple[str, ...]:
        return tuple(sorted(self._paused_traces))

    def snapshot(self) -> dict[str, int]:
        return {
            "depth": self.depth,
            "in_flight": self.in_flight,
            "high_watermark": self._high_watermark,
            "history_size": len(self._history),
            "paused_channel_count": len(self._paused_channels),
            "paused_trace_count": len(self._paused_traces),
        }

    def get_task(self, task_id: str) -> OutputTask | None:
        return self._history.get(task_id) or self._in_flight.get(task_id)

    def compact(self) -> dict[str, int]:
        stale_heap_entries = len(self._heap) - self.depth
        if stale_heap_entries > 0:
            self._heap = [entry for entry in self._heap if entry.task.status == "pending"]
            heapq.heapify(self._heap)
        history_before = len(self._history)
        self._prune_history()
        return {
            "stale_heap_entries_removed": max(stale_heap_entries, 0),
            "history_entries_removed": max(history_before - len(self._history), 0),
            "pending_depth": self.depth,
            "history_size": len(self._history),
        }

    def pause_channel(self, channel: str) -> bool:
        normalized = str(channel).strip()
        if not normalized:
            raise OutputQueueError("channel must not be empty")
        if normalized in self._paused_channels:
            return False
        self._paused_channels.add(normalized)
        return True

    def resume_channel(self, channel: str) -> bool:
        normalized = str(channel).strip()
        if not normalized:
            raise OutputQueueError("channel must not be empty")
        if normalized not in self._paused_channels:
            return False
        self._paused_channels.remove(normalized)
        return True

    def is_channel_paused(self, channel: str) -> bool:
        return str(channel).strip() in self._paused_channels

    def pause_trace(self, trace_id: str) -> bool:
        normalized = str(trace_id).strip()
        if not normalized:
            raise OutputQueueError("trace_id must not be empty")
        if normalized in self._paused_traces:
            return False
        self._paused_traces.add(normalized)
        return True

    def resume_trace(self, trace_id: str) -> bool:
        normalized = str(trace_id).strip()
        if not normalized:
            raise OutputQueueError("trace_id must not be empty")
        if normalized not in self._paused_traces:
            return False
        self._paused_traces.remove(normalized)
        return True

    def is_trace_paused(self, trace_id: str) -> bool:
        return str(trace_id).strip() in self._paused_traces

    def _remember_history(self, task: OutputTask) -> None:
        if self.history_retention <= 0:
            self._history.pop(task.task_id, None)
            return
        if task.task_id not in self._history:
            self._history_order.append(task.task_id)
        self._history[task.task_id] = task
        self._prune_history()

    def _prune_history(self) -> None:
        if self.history_retention <= 0:
            self._history.clear()
            self._history_order.clear()
            return
        while len(self._history_order) > self.history_retention:
            stale_task_id = self._history_order.pop(0)
            self._history.pop(stale_task_id, None)
