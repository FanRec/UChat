from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from queue import Queue
from typing import Any

from services.tts_bridge.models import PlaybackProgressSnapshot


def pcm_bytes_for_ms(sample_rate: int, channels: int, milliseconds: int) -> int:
    bytes_per_ms = max(sample_rate * channels * 2 / 1000, 1)
    return max(int(milliseconds * bytes_per_ms), 0)


def playback_timeout_ms(duration_ms: int, drain_timeout_ms: int) -> int:
    return max(duration_ms + 250, drain_timeout_ms, 750)


class AudioPlayback:
    """音频播放器。

    两种模式：
    - 流式模式（sounddevice）：声卡立即启动，预填静音，feed() 到了就播。
    - 文件模式（winsound 回退）：enqueue(path) 排队播放完整 WAV 文件。
    """

    def __init__(self, device: str | None = None) -> None:
        self._device = device
        self._sd = None
        self._stream = None
        self._buffer: bytearray = bytearray()
        self._buffer_lock = threading.Lock()
        self._streaming = False
        self._stream_sample_rate = 0
        self._stream_channels = 1
        self._stream_finished = False
        self._stream_started_at_ms: float | None = None
        self._first_feed_at_ms: float | None = None
        self._playback_started_at_ms: float | None = None
        self._playback_started = False
        self._played_bytes = 0
        self._underrun_count = 0
        self._prebuffer_bytes = 0
        self._rebuffer_bytes = 0
        self._low_water_bytes = 0
        self._rebuffering = False
        self._rebuffer_count = 0
        self._drained = threading.Event()
        self._queue: Queue[tuple[Path, threading.Event | None]] = Queue()
        self._file_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current_file: Path | None = None
        self._sd_available = self._check_sounddevice()
        self._winsound_available = self._check_winsound()

    @staticmethod
    def _check_sounddevice() -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("sounddevice") is not None
        except (ImportError, ValueError):
            return False

    @staticmethod
    def _check_winsound() -> bool:
        try:
            import winsound  # noqa: F401

            return True
        except ImportError:
            return False

    def start_stream(
        self,
        sample_rate: int,
        channels: int = 1,
        *,
        prebuffer_ms: int = 160,
        rebuffer_ms: int = 220,
    ) -> None:
        if not self._sd_available:
            return
        if self._sd is None:
            import sounddevice as sd

            self._sd = sd
        self.stop_stream()
        with self._buffer_lock:
            self._buffer.clear()
            self._stream_finished = False
            self._stream_started_at_ms = round(time.perf_counter() * 1000, 2)
            self._first_feed_at_ms = None
            self._playback_started_at_ms = None
            self._playback_started = False
            self._stream_sample_rate = sample_rate
            self._stream_channels = channels
            self._played_bytes = 0
            self._underrun_count = 0
            self._rebuffering = False
            self._rebuffer_count = 0
            self._prebuffer_bytes = pcm_bytes_for_ms(sample_rate, channels, prebuffer_ms)
            self._rebuffer_bytes = max(pcm_bytes_for_ms(sample_rate, channels, rebuffer_ms), self._prebuffer_bytes)
            self._low_water_bytes = max(
                min(self._prebuffer_bytes // 2, self._rebuffer_bytes // 2),
                pcm_bytes_for_ms(sample_rate, channels, 20),
            )
            self._drained.clear()
        self._streaming = True
        latency_seconds = max(min(prebuffer_ms / 1000, 0.12), 0.04)
        self._stream = self._sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            device=self._device,
            latency=latency_seconds,
            callback=self._audio_callback,
        )
        self._stream.start()

    def _audio_callback(self, outdata, frame_count, time_info, status) -> None:
        import numpy as np

        outdata.fill(0)
        needed = outdata.size * 2
        with self._buffer_lock:
            if not self._streaming:
                return
            if not self._playback_started:
                can_start = len(self._buffer) >= self._prebuffer_bytes or (self._stream_finished and bool(self._buffer))
                if can_start:
                    self._playback_started = True
                    self._playback_started_at_ms = round(time.perf_counter() * 1000, 2)
                elif self._stream_finished and not self._buffer:
                    self._drained.set()
                    return
                else:
                    return
            if self._rebuffering:
                can_resume = len(self._buffer) >= self._rebuffer_bytes or (self._stream_finished and bool(self._buffer))
                if can_resume:
                    self._rebuffering = False
                elif self._stream_finished and not self._buffer:
                    self._drained.set()
                    return
                else:
                    return
            elif not self._stream_finished and len(self._buffer) < self._low_water_bytes:
                self._rebuffering = True
                self._rebuffer_count += 1
                return
            available = len(self._buffer)
            if available < needed and not self._stream_finished:
                self._rebuffering = True
                self._rebuffer_count += 1
                return
            take = min(available, needed)
            chunk = bytes(self._buffer[:take])
            if take:
                del self._buffer[:take]
                self._played_bytes += take
            if take < needed and self._stream_finished:
                self._underrun_count += 1
            if self._stream_finished and not self._buffer:
                self._drained.set()
        pcm = np.frombuffer(chunk, dtype="int16")
        if len(pcm):
            outdata.flat[: len(pcm)] = pcm

    def feed(self, pcm_bytes: bytes) -> None:
        if not self._streaming:
            return
        with self._buffer_lock:
            if self._first_feed_at_ms is None:
                self._first_feed_at_ms = round(time.perf_counter() * 1000, 2)
            self._buffer.extend(pcm_bytes)

    def finish_stream(self) -> None:
        with self._buffer_lock:
            self._stream_finished = True
            if not self._buffer:
                self._drained.set()

    def wait_until_drained(self, timeout_ms: int) -> bool:
        return self._drained.wait(timeout=max(timeout_ms / 1000, 0.0))

    def stream_stats(self) -> dict[str, Any]:
        with self._buffer_lock:
            first_chunk_latency_ms = None
            playback_start_latency_ms = None
            if self._stream_started_at_ms is not None and self._first_feed_at_ms is not None:
                first_chunk_latency_ms = round(self._first_feed_at_ms - self._stream_started_at_ms, 2)
            if self._stream_started_at_ms is not None and self._playback_started_at_ms is not None:
                playback_start_latency_ms = round(self._playback_started_at_ms - self._stream_started_at_ms, 2)
            return {
                "first_chunk_latency_ms": first_chunk_latency_ms,
                "playback_start_latency_ms": playback_start_latency_ms,
                "playback_started": self._playback_started,
                "underrun_count": self._underrun_count,
                "rebuffer_count": self._rebuffer_count,
                "buffered_bytes": len(self._buffer),
            }

    def progress_snapshot(self, *, duration_ms: int) -> PlaybackProgressSnapshot:
        with self._buffer_lock:
            played_samples = 0
            if self._stream_channels > 0:
                played_samples = self._played_bytes // max(self._stream_channels * 2, 1)
            played_ms = 0.0
            if self._stream_sample_rate > 0:
                played_ms = played_samples / self._stream_sample_rate * 1000
            buffered_ms = 0.0
            if self._stream_sample_rate > 0 and self._stream_channels > 0:
                buffered_samples = len(self._buffer) // max(self._stream_channels * 2, 1)
                buffered_ms = buffered_samples / self._stream_sample_rate * 1000
            return PlaybackProgressSnapshot(
                playback_started=self._playback_started,
                played_ms=min(round(played_ms, 2), float(duration_ms)),
                played_samples=played_samples,
                buffered_ms=round(buffered_ms, 2),
                segment_finished=bool(self._stream_finished and not self._buffer and self._drained.is_set()),
                duration_ms=duration_ms,
            )

    def stop_stream(self) -> None:
        self._streaming = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._buffer_lock:
            self._stream_finished = True
            self._buffer.clear()
            self._played_bytes = 0
            self._stream_sample_rate = 0
            self._stream_channels = 1
            self._drained.set()

    @property
    def is_streaming(self) -> bool:
        return self._streaming and self._stream is not None

    @property
    def supports_streaming(self) -> bool:
        return self._sd_available

    def start(self) -> None:
        if not self._winsound_available:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tts-audio-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.stop_stream()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def enqueue(self, path: Path) -> None:
        self.enqueue_and_wait(path, wait=False)

    def enqueue_and_wait(self, path: Path, *, wait: bool = True, timeout_ms: int | None = None) -> bool:
        if not self._winsound_available:
            return False
        completed = threading.Event() if wait else None
        self._queue.put((path, completed))
        self._wake.set()
        if completed is None:
            return True
        timeout_seconds = None if timeout_ms is None else max(timeout_ms / 1000, 0.0)
        return completed.wait(timeout=timeout_seconds)

    def play_pcm_and_wait(self, *, pcm_bytes: bytes, sample_rate: int, timeout_ms: int) -> bool:
        if not self._sd_available:
            return False
        self.start_stream(sample_rate=sample_rate, prebuffer_ms=60, rebuffer_ms=120)
        self.feed(pcm_bytes)
        self.finish_stream()
        return self.wait_until_drained(timeout_ms)

    def play_file_as_pcm_and_wait(self, *, path: Path, timeout_ms: int, prebuffer_ms: int = 60, rebuffer_ms: int = 120) -> bool:
        if not self._sd_available:
            return False
        try:
            with wave.open(str(path), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                pcm_bytes = wav_file.readframes(wav_file.getnframes())
        except (wave.Error, FileNotFoundError):
            return False
        if sample_rate <= 0 or channels <= 0:
            return False
        self.start_stream(sample_rate=sample_rate, channels=channels, prebuffer_ms=prebuffer_ms, rebuffer_ms=rebuffer_ms)
        self.feed(pcm_bytes)
        self.finish_stream()
        return self.wait_until_drained(timeout_ms)

    def cancel_all(self) -> None:
        with self._file_lock:
            while not self._queue.empty():
                try:
                    _, completed = self._queue.get_nowait()
                except Exception:
                    break
                if completed is not None:
                    completed.set()
        self.stop_stream()

    @property
    def is_playing(self) -> bool:
        return self._current_file is not None or self._streaming

    @property
    def ready(self) -> bool:
        return self._sd_available or self._winsound_available

    def _run(self) -> None:
        import winsound

        while not self._stop.is_set():
            self._wake.wait(timeout=0.1)
            self._wake.clear()
            while not self._stop.is_set():
                if self._queue.empty():
                    break
                path, completed = self._queue.get()
                self._current_file = path
                try:
                    winsound.PlaySound(str(path), winsound.SND_FILENAME)
                except Exception:
                    pass
                finally:
                    self._current_file = None
                    if completed is not None:
                        completed.set()
