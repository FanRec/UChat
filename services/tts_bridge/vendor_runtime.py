from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator

import httpx


class VendorRuntime:
    def __init__(self, config, *, client: httpx.Client | None = None, config_error: type[Exception] = RuntimeError) -> None:
        self.config = config
        self._config_error = config_error
        self.process: subprocess.Popen[str] | None = None
        self.client = client or httpx.Client(base_url=f"http://127.0.0.1:{config.vendor_port}", trust_env=False)
        self._vendor_log_path: Path | None = None

    @property
    def vendor_base_url(self) -> str:
        return f"http://127.0.0.1:{self.config.vendor_port}"

    def ensure_running(self) -> None:
        if self.ready():
            return
        if self.process is not None and self.process.poll() is None:
            return
        self._cleanup_process()
        self._wait_for_port_free()
        entry_script = self.config.vendor.entry_script.resolve()
        cmd = [self.config.vendor.python_executable, str(entry_script), "-a", "127.0.0.1", "-p", str(self.config.vendor_port)]
        if self.config.vendor.api_style == "legacy":
            if self.config.vendor.gpt_model_path is None or self.config.vendor.sovits_model_path is None:
                raise self._config_error("legacy vendor requires gpt_model_path and sovits_model_path")
            if self.config.preset.ref_audio_path is None:
                raise self._config_error("legacy vendor requires preset.ref_audio_path")
            cmd.extend(
                [
                    "-d",
                    self.config.vendor.device,
                    "-g",
                    str(self.config.vendor.gpt_model_path.resolve()),
                    "-s",
                    str(self.config.vendor.sovits_model_path.resolve()),
                    "-dr",
                    str(self.config.preset.ref_audio_path.resolve()),
                    "-dt",
                    self.config.preset.prompt_text,
                    "-dl",
                    self.config.preset.prompt_lang,
                ]
            )
        else:
            if self.config.vendor.tts_config_path is None:
                raise self._config_error("api_v2 vendor requires tts_config_path")
            cmd.extend(["-c", str(self.config.vendor.tts_config_path.resolve())])
        log_dir = self.config.output_dir.resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._vendor_log_path = log_dir / "vendor_stderr.log"
        stderr_file = self._vendor_log_path.open("a", encoding="utf-8")
        print(f"[TTS] starting vendor: {' '.join(cmd[:3])}...", flush=True)
        self.process = subprocess.Popen(
            cmd,
            cwd=str(entry_script.parent),
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
        )

    def _cleanup_process(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None

    def _wait_for_port_free(self, timeout: float = 10.0) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.config.vendor_port), timeout=0.3):
                    time.sleep(0.5)
            except (ConnectionRefusedError, OSError):
                return
        print(f"[TTS] warning: port {self.config.vendor_port} still in use after {timeout}s", flush=True)

    def ready(self) -> bool:
        try:
            response = self.client.get("/docs", timeout=0.5)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def wait_until_ready(self, timeout_ms: int) -> bool:
        deadline = time.perf_counter() + (timeout_ms / 1000)
        while time.perf_counter() < deadline:
            if self.ready():
                return True
            if self.process is not None and self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return self.ready()

    def synthesize(self, payload: dict[str, Any], *, timeout_ms: int) -> httpx.Response:
        endpoint = "/" if self.config.vendor.api_style == "legacy" else "/tts"
        return self.client.post(
            endpoint,
            json=payload,
            timeout=max(timeout_ms / 1000, 0.1),
        )

    def synthesize_stream(self, payload: dict[str, Any], *, timeout_ms: int) -> Iterator[bytes]:
        endpoint = "/" if self.config.vendor.api_style == "legacy" else "/tts"
        timeout = max(timeout_ms / 1000, 0.1)
        with self.client.stream("POST", endpoint, json=payload, timeout=timeout) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    def close(self) -> None:
        self.client.close()
        self._cleanup_process()
