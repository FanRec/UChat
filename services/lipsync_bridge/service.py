from __future__ import annotations

import argparse
import base64
import wave
import sys
import threading
import time
import tomllib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from services.tts_bridge.audio_playback import AudioPlayback, playback_timeout_ms


class LipsyncBridgeConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceBindConfig:
    service_name: str
    base_url: str
    host: str
    port: int


@dataclass(frozen=True)
class OutputConfig:
    device: str | None
    max_queue_size: int
    drain_timeout_ms: int
    sample_rate: int | None


@dataclass(frozen=True)
class LipsyncBridgeConfig:
    service: ServiceBindConfig
    output: OutputConfig

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LipsyncBridgeConfig":
        config_path = Path(path or default_config_path())
        if not config_path.exists():
            raise LipsyncBridgeConfigError(f"config file not found: {config_path}")
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
        output_raw = raw.get("output", {})
        if not isinstance(output_raw, dict):
            output_raw = {}
        device_raw = str(output_raw.get("device", "")).strip()
        return cls(
            service=ServiceBindConfig(
                service_name=str(raw.get("service_name", "lipsync_bridge")).strip() or "lipsync_bridge",
                base_url=str(raw.get("base_url", "http://127.0.0.1:8105")).strip().rstrip("/"),
                host=str(raw.get("host", "127.0.0.1")).strip() or "127.0.0.1",
                port=int(raw.get("port", 8105)),
            ),
            output=OutputConfig(
                device=device_raw or None,
                max_queue_size=max(1, int(output_raw.get("max_queue_size", 8) or 8)),
                drain_timeout_ms=max(250, int(output_raw.get("drain_timeout_ms", 2500) or 2500)),
                sample_rate=(
                    max(1, int(output_raw.get("sample_rate", 0) or 0))
                    if int(output_raw.get("sample_rate", 0) or 0) > 0
                    else None
                ),
            ),
        )


@dataclass(slots=True)
class MirrorTask:
    trace_id: str
    task_id: str
    generation_id: int
    segment_index: int
    text: str
    sample_rate: int
    duration_ms: int
    media_type: str
    audio_path: Path | None = None
    pcm_bytes: bytes | None = None
    retry_count: int = 0


class MirrorRequest(BaseModel):
    trace_id: str
    task_id: str
    generation_id: int = 1
    segment_index: int = 1
    text: str = ""
    sample_rate: int = 32000
    duration_ms: int = 0
    media_type: str = "wav"
    audio_path: str | None = None
    pcm_base64: str | None = None


class CancelTraceRequest(BaseModel):
    trace_id: str


class TurnEndRequest(BaseModel):
    trace_id: str
    generation_id: int | None = None
    last_segment_index: int | None = None


class LipsyncBridgeService:
    def __init__(self, config: LipsyncBridgeConfig, *, player: AudioPlayback | None = None) -> None:
        self.config = config
        self._resolved_output_device = config.output.device or ""
        self._resolved_output_device_index: int | None = None
        self._resolved_output_sample_rate: int | None = config.output.sample_rate
        self._resolved_output_hostapi: str = ""
        self._available_devices_cache: list[dict[str, Any]] = []
        self._device_candidates = self._build_device_candidates(config.output.device)
        self.player = player or AudioPlayback(device=self._current_device_selector())
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._pending: deque[MirrorTask] = deque()
        self._current: MirrorTask | None = None
        self._current_cancelled = False
        self._active_generation_by_trace: dict[str, int] = {}
        self._worker: threading.Thread | None = None
        self._stop = False
        self._completed_count = 0
        self._cancelled_count = 0
        self._dropped_count = 0
        self._accepted_count = 0
        self._ignored_stale_count = 0
        self._last_error = ""
        self._last_turn_end: dict[str, dict[str, int | None]] = {}
        self._last_request_status = ""
        self._last_request_trace_id = ""
        self._last_request_generation_id: int | None = None
        if player is None and self._resolved_output_device_index is None and config.output.device:
            self._last_error = f"output device not resolved: {config.output.device}"

    def start(self) -> None:
        self.player.start()
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop = False
            self._worker = threading.Thread(target=self._run_worker, name="lipsync-bridge-worker", daemon=True)
            self._worker.start()

    def close(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)
        self._worker = None
        self.player.cancel_all()
        self.player.stop()

    def health(self) -> dict[str, Any]:
        with self._lock:
            current = self._current
            queue_depth = len(self._pending)
            last_error = self._last_error
            completed_count = self._completed_count
            cancelled_count = self._cancelled_count
            dropped_count = self._dropped_count
            accepted_count = self._accepted_count
            ignored_stale_count = self._ignored_stale_count
            last_request_status = self._last_request_status
            last_request_trace_id = self._last_request_trace_id
            last_request_generation_id = self._last_request_generation_id
        devices = list(self._available_devices())
        return {
            "service_ready": True,
            "output_device": self.config.output.device or "",
            "resolved_output_device": self._resolved_output_device,
            "resolved_output_device_index": self._resolved_output_device_index,
            "resolved_output_sample_rate": self._resolved_output_sample_rate,
            "resolved_output_hostapi": self._resolved_output_hostapi,
            "player_ready": bool(getattr(self.player, "supports_streaming", False)),
            "queue_depth": queue_depth,
            "current_task_id": current.task_id if current is not None else "",
            "current_trace_id": current.trace_id if current is not None else "",
            "current_generation_id": current.generation_id if current is not None else None,
            "completed_count": completed_count,
            "cancelled_count": cancelled_count,
            "dropped_count": dropped_count,
            "accepted_count": accepted_count,
            "ignored_stale_count": ignored_stale_count,
            "last_error": last_error,
            "last_request_status": last_request_status,
            "last_request_trace_id": last_request_trace_id,
            "last_request_generation_id": last_request_generation_id,
            "available_output_device_count": len(devices),
        }

    def list_devices(self) -> dict[str, Any]:
        return {"devices": self._available_devices()}

    def mirror(self, request: MirrorRequest) -> dict[str, Any]:
        if not request.audio_path and not request.pcm_base64:
            raise HTTPException(status_code=400, detail="audio_path or pcm_base64 is required")
        audio_path = Path(request.audio_path).resolve() if request.audio_path else None
        if audio_path is not None and not audio_path.exists():
            raise HTTPException(status_code=400, detail=f"audio_path not found: {audio_path}")
        pcm_bytes = None
        if request.pcm_base64:
            try:
                pcm_bytes = base64.b64decode(request.pcm_base64.encode("ascii"), validate=True)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid pcm_base64: {exc}") from exc
        generation_id = max(int(request.generation_id or 1), 1)
        task = MirrorTask(
            trace_id=request.trace_id,
            task_id=request.task_id,
            generation_id=generation_id,
            segment_index=max(int(request.segment_index or 1), 1),
            text=request.text,
            sample_rate=max(int(request.sample_rate or 32000), 1),
            duration_ms=max(int(request.duration_ms or 0), 0),
            media_type=request.media_type,
            audio_path=audio_path,
            pcm_bytes=pcm_bytes,
        )
        with self._condition:
            active_generation = self._active_generation_by_trace.get(task.trace_id, 1)
            if generation_id < active_generation:
                self._record_request_status(
                    status="ignored_stale_generation",
                    trace_id=task.trace_id,
                    generation_id=generation_id,
                )
                self._ignored_stale_count += 1
                return {"status": "ignored", "reason": "stale_generation", "task_id": task.task_id}
            self._active_generation_by_trace[task.trace_id] = max(active_generation, generation_id)
            if len(self._pending) >= self.config.output.max_queue_size:
                self._dropped_count += 1
                self._record_request_status(
                    status="dropped_busy",
                    trace_id=task.trace_id,
                    generation_id=generation_id,
                )
                return {"status": "dropped_busy", "task_id": task.task_id}
            self._pending.append(task)
            self._accepted_count += 1
            self._record_request_status(
                status="accepted",
                trace_id=task.trace_id,
                generation_id=generation_id,
            )
            self._condition.notify_all()
        return {"status": "accepted", "task_id": task.task_id}

    def cancel_trace(self, request: CancelTraceRequest) -> dict[str, Any]:
        cancelled_pending = 0
        should_stop_current = False
        with self._condition:
            trace_known = request.trace_id in self._active_generation_by_trace
            retained: deque[MirrorTask] = deque()
            for task in self._pending:
                if task.trace_id == request.trace_id:
                    cancelled_pending += 1
                else:
                    retained.append(task)
            self._pending = retained
            if self._current is not None and self._current.trace_id == request.trace_id:
                should_stop_current = True
                self._current_cancelled = True
                trace_known = True
            if trace_known:
                next_generation = self._active_generation_by_trace.get(request.trace_id, 1) + 1
                self._active_generation_by_trace[request.trace_id] = next_generation
        if should_stop_current:
            self.player.cancel_all()
        cancelled_total = cancelled_pending + (1 if should_stop_current else 0)
        with self._lock:
            self._cancelled_count += cancelled_total
            self._record_request_status(status="cancel_trace", trace_id=request.trace_id, generation_id=None)
        return {
            "status": "cancelled" if cancelled_total > 0 else "accepted",
            "trace_id": request.trace_id,
            "cancelled_task_count": cancelled_total,
        }

    def turn_end(self, request: TurnEndRequest) -> dict[str, Any]:
        with self._lock:
            self._last_turn_end[request.trace_id] = {
                "generation_id": request.generation_id,
                "last_segment_index": request.last_segment_index,
            }
            self._record_request_status(
                status="turn_end",
                trace_id=request.trace_id,
                generation_id=request.generation_id,
            )
        return {"status": "accepted", "trace_id": request.trace_id}

    def _run_worker(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stop:
                    self._condition.wait(timeout=0.2)
                if self._stop:
                    return
                task = self._pending.popleft()
                active_generation = self._active_generation_by_trace.get(task.trace_id, 1)
                if task.generation_id < active_generation:
                    self._cancelled_count += 1
                    continue
                self._current = task
                self._current_cancelled = False
            completed = False
            try:
                timeout_ms = playback_timeout_ms(task.duration_ms, self.config.output.drain_timeout_ms)
                if task.audio_path is not None:
                    completed = self._play_audio_file(task.audio_path, timeout_ms=timeout_ms)
                elif task.pcm_bytes is not None:
                    sample_rate = self._resolved_output_sample_rate or task.sample_rate
                    pcm_bytes = task.pcm_bytes
                    if self._resolved_output_sample_rate and task.sample_rate != self._resolved_output_sample_rate:
                        pcm_bytes = _resample_pcm_mono_int16(
                            task.pcm_bytes,
                            source_rate=task.sample_rate,
                            target_rate=self._resolved_output_sample_rate,
                        )
                    completed = self.player.play_pcm_and_wait(
                        pcm_bytes=pcm_bytes,
                        sample_rate=sample_rate,
                        timeout_ms=timeout_ms,
                    )
                else:
                    self._set_last_error(f"mirror task has no playable payload: {task.task_id}")
            except Exception as exc:
                if self._try_advance_output_candidate(str(exc), task):
                    continue
                self._set_last_error(str(exc))
            finally:
                self.player.stop_stream()
                with self._lock:
                    was_cancelled = self._current_cancelled
                    self._current = None
                    self._current_cancelled = False
                    if was_cancelled:
                        self._cancelled_count += 1
                    elif completed:
                        self._completed_count += 1

    def _set_last_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def _record_request_status(self, *, status: str, trace_id: str, generation_id: int | None) -> None:
        self._last_request_status = status
        self._last_request_trace_id = trace_id
        self._last_request_generation_id = generation_id

    def _play_audio_file(self, path: Path, *, timeout_ms: int) -> bool:
        try:
            with wave.open(str(path), "rb") as wav_file:
                sample_rate = int(wav_file.getframerate() or 0)
                channels = int(wav_file.getnchannels() or 0)
                sample_width = int(wav_file.getsampwidth() or 0)
                pcm_bytes = wav_file.readframes(wav_file.getnframes())
        except Exception:
            return self.player.play_file_as_pcm_and_wait(path=path, timeout_ms=timeout_ms)
        if sample_rate <= 0 or channels <= 0 or sample_width != 2 or channels != 1:
            return self.player.play_file_as_pcm_and_wait(path=path, timeout_ms=timeout_ms)
        target_rate = self._resolved_output_sample_rate or sample_rate
        if target_rate != sample_rate:
            pcm_bytes = _resample_pcm_mono_int16(pcm_bytes, source_rate=sample_rate, target_rate=target_rate)
        return self.player.play_pcm_and_wait(
            pcm_bytes=pcm_bytes,
            sample_rate=target_rate,
            timeout_ms=timeout_ms,
        )

    def _build_device_candidates(self, requested_name: str | None) -> list[dict[str, Any]]:
        normalized = (requested_name or "").strip()
        if not normalized:
            self._available_devices_cache = self._query_output_devices()
            return []
        devices = self._query_output_devices()
        self._available_devices_cache = devices
        matches = [item for item in devices if str(item.get("name", "")).strip().lower() == normalized.lower()]
        if not matches:
            matches = [item for item in devices if normalized.lower() in str(item.get("name", "")).strip().lower()]
        return sorted(
            matches,
            key=lambda item: (
                _hostapi_priority(int(item.get("hostapi", -1) or -1)),
                -int(item.get("max_output_channels", 0) or 0),
                int(item.get("index", 0) or 0),
            ),
        )

    def _current_device_selector(self) -> str | int | None:
        if not self._device_candidates:
            return self.config.output.device
        picked = self._device_candidates[0]
        self._resolved_output_device = str(picked.get("name", "")).strip()
        self._resolved_output_device_index = int(picked.get("index", -1))
        self._resolved_output_hostapi = str(picked.get("hostapi_name", "")).strip()
        if self.config.output.sample_rate is None:
            try:
                self._resolved_output_sample_rate = int(round(float(picked.get("default_samplerate", 0.0) or 0.0))) or None
            except Exception:
                self._resolved_output_sample_rate = None
        return self._resolved_output_device_index

    def _try_advance_output_candidate(self, message: str, task: MirrorTask) -> bool:
        if task.retry_count > 0:
            return False
        if len(self._device_candidates) <= 1:
            return False
        failed_index = self._resolved_output_device_index
        failed_hostapi = self._resolved_output_hostapi or "unknown"
        self._device_candidates = self._device_candidates[1:]
        next_selector = self._current_device_selector()
        self.player = AudioPlayback(device=next_selector)
        task.retry_count += 1
        with self._condition:
            self._pending.appendleft(task)
            self._condition.notify_all()
        self._set_last_error(
            "fallback output candidate after open failure: "
            f"index={failed_index} hostapi={failed_hostapi} error={message}"
        )
        return True

    def _available_devices(self) -> list[dict[str, Any]]:
        if self._available_devices_cache:
            return list(self._available_devices_cache)
        devices = self._query_output_devices()
        self._available_devices_cache = devices
        return list(devices)

    def _query_output_devices(self) -> list[dict[str, Any]]:
        try:
            import sounddevice as sd
        except Exception:
            return []
        default_device_index = None
        try:
            default_device = sd.default.device
            if isinstance(default_device, (list, tuple)) and len(default_device) >= 2:
                default_device_index = default_device[1]
            elif isinstance(default_device, int):
                default_device_index = default_device
        except Exception:
            default_device_index = None
        devices: list[dict[str, Any]] = []
        try:
            for index, item in enumerate(sd.query_devices()):
                if int(item.get("max_output_channels", 0) or 0) <= 0:
                    continue
                hostapi_index = int(item.get("hostapi", -1) or -1)
                hostapi_name = ""
                try:
                    hostapi_name = str(sd.query_hostapis(hostapi_index).get("name", "")).strip()
                except Exception:
                    hostapi_name = ""
                devices.append(
                    {
                        "index": index,
                        "name": str(item.get("name", "")).strip(),
                        "hostapi": hostapi_index,
                        "hostapi_name": hostapi_name,
                        "max_output_channels": int(item.get("max_output_channels", 0) or 0),
                        "default_samplerate": float(item.get("default_samplerate", 0.0) or 0.0),
                        "is_default_output": default_device_index == index,
                    }
                )
        except Exception:
            return []
        return devices


def build_app(service: LipsyncBridgeService) -> FastAPI:
    app = FastAPI(title="lipsync_bridge")

    @app.on_event("startup")
    def startup() -> None:
        service.start()

    @app.on_event("shutdown")
    def shutdown() -> None:
        service.close()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return service.health()

    @app.get("/v1/lipsync/devices")
    def list_devices() -> dict[str, Any]:
        return service.list_devices()

    @app.post("/v1/lipsync/mirror")
    def mirror(request: MirrorRequest) -> dict[str, Any]:
        return service.mirror(request)

    @app.post("/v1/lipsync/cancel-trace")
    def cancel_trace(request: CancelTraceRequest) -> dict[str, Any]:
        return service.cancel_trace(request)

    @app.post("/v1/lipsync/turn-end")
    def turn_end(request: TurnEndRequest) -> dict[str, Any]:
        return service.turn_end(request)

    return app


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "service.toml"


def _hostapi_priority(hostapi: int) -> int:
    priority = {
        2: 0,  # Windows WASAPI
        3: 1,  # Windows WDM-KS
        1: 2,  # Windows DirectSound
        0: 3,  # MME
    }
    return priority.get(hostapi, 9)


def _resample_pcm_mono_int16(pcm_bytes: bytes, *, source_rate: int, target_rate: int) -> bytes:
    if not pcm_bytes or source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return pcm_bytes
    import audioop

    converted, _ = audioop.ratecv(pcm_bytes, 2, 1, source_rate, target_rate, None)
    return converted


def build_service(config_path: str | Path | None = None) -> LipsyncBridgeService:
    return LipsyncBridgeService(LipsyncBridgeConfig.load(config_path or default_config_path()))


def run_server(config_path: str | Path | None = None) -> None:
    config = LipsyncBridgeConfig.load(config_path or default_config_path())
    uvicorn.run(build_app(LipsyncBridgeService(config)), host=config.service.host, port=config.service.port, log_level="info")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UChat lipsync_bridge service")
    parser.add_argument("--serve", action="store_true", help="Run the lipsync mirror HTTP service")
    parser.add_argument("--config", default=str(default_config_path()), help="Path to service.toml")
    return parser.parse_known_args(argv)[0]


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    if args.serve:
        run_server(args.config)
    else:
        print(f"lipsync_bridge ready: {default_config_path()}")
