from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from services.obs_bridge.config import ObsBridgeConfig, OverlayConfig
from services.obs_bridge.subtitle_state import SubtitleStateStore

logger = logging.getLogger("services.obs_bridge")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObsBridgeService:
    def __init__(self, config: ObsBridgeConfig) -> None:
        self.config = config
        self.overlay_config = config.overlay
        self._connections: dict[WebSocket, asyncio.AbstractEventLoop] = {}
        self._state = SubtitleStateStore()
        self._lock = threading.Lock()

    # ── HTTP handlers ──────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        with self._lock:
            ws_count = len(self._connections)
        return {
            "status": "ok",
            "service": self.config.service.service_name,
            "ws_connections": ws_count,
            "overlay": {
                "max_lines": self.overlay_config.max_lines,
                "position": self.overlay_config.position,
                "style_preset": self.overlay_config.style_preset,
            },
        }

    def handle_subtitle(self, body: dict[str, Any]) -> dict[str, Any]:
        trace_id = str(body.get("trace_id", ""))
        task_id = str(body.get("task_id", body.get("outbound_id", "")))
        text = str(body.get("text", ""))
        action = str(body.get("action", "sentence"))
        session = self._state.apply(body)
        msg: dict[str, Any] = {
            "type": "subtitle_state",
            "action": action,
            "trace_id": trace_id,
            "task_id": task_id,
            "timestamp": _now_iso(),
            "state": session.to_dict() if session is not None else None,
        }

        _debug_subtitle(text or f"[{action}]", trace_id, action)
        self._broadcast(msg)

        return {"status": "accepted", "action": action, "trace_id": trace_id}

    def handle_status(self, body: dict[str, Any]) -> dict[str, Any]:
        trace_id = str(body.get("trace_id", ""))
        text = str(body.get("text", ""))
        msg = {
            "type": "status",
            "trace_id": trace_id,
            "text": text,
            "metadata": body.get("metadata") or {},
            "timestamp": _now_iso(),
        }
        _debug_subtitle(f"[status] {text}", trace_id, "status")
        self._broadcast(msg)
        return {"status": "accepted"}

    def handle_cancel(self, body: dict[str, Any]) -> dict[str, Any]:
        task_id = str(body.get("task_id", ""))
        trace_id = str(body.get("trace_id", ""))
        session = self._state.apply({"trace_id": trace_id, "task_id": task_id, "action": "clear", "metadata": {"generation_id": int((body.get("metadata") or {}).get("generation_id", 1) or 1)}})
        msg = {
            "type": "subtitle_state",
            "action": "clear",
            "trace_id": trace_id,
            "task_id": task_id,
            "timestamp": _now_iso(),
            "state": session.to_dict() if session is not None else None,
        }
        self._broadcast(msg)
        return {"status": "cancelled", "task_id": task_id}

    def get_active_subtitles(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for session in self._state.active_sessions():
            text = ""
            task_id = ""
            if session.active_line is not None:
                task_id = session.active_line.task_id
                text = session.active_line.revealed_text or session.active_line.text
            elif session.history:
                task_id = session.history[-1].task_id
                text = session.history[-1].revealed_text or session.history[-1].text
            items.append(
                {
                    "type": "subtitle_state",
                    "action": "replay",
                    "trace_id": session.trace_id,
                    "task_id": task_id,
                    "text": text,
                    "timestamp": _now_iso(),
                    "state": session.to_dict(),
                }
            )
        return items

    # ── WebSocket management ───────────────────────────────────────

    def register_ws(self, ws: WebSocket) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        with self._lock:
            self._connections[ws] = loop
        _debug_ws(f"client connected (total={len(self._connections)})")

    def unregister_ws(self, ws: WebSocket) -> None:
        with self._lock:
            self._connections.pop(ws, None)
        _debug_ws(f"client disconnected (total={len(self._connections)})")

    def _broadcast(self, msg: dict[str, Any]) -> None:
        msg_json = json.dumps(msg, ensure_ascii=False)
        with self._lock:
            items = list(self._connections.items())
        for ws, loop in items:
            try:
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws.send_text(msg_json), loop)
                else:
                    loop.create_task(ws.send_text(msg_json))
            except Exception:
                with self._lock:
                    self._connections.pop(ws, None)


# ── Rich debug output ─────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich import box

    _CONSOLE = Console(stderr=True)
except ImportError:
    _CONSOLE = None
    box = None


def _debug_subtitle(text: str, trace_id: str, action: str) -> None:
    if _CONSOLE is None:
        return
    display = text if len(text) <= 80 else text[:77] + "..."
    _CONSOLE.print(Panel(
        f"[white]{display}[/]\n[dim]trace={trace_id}  action={action}[/]",
        title="[bold cyan]OBS Subtitle[/]",
        border_style="cyan",
        box=box.ROUNDED,
    ))


def _debug_ws(message: str) -> None:
    if _CONSOLE is None:
        return
    _CONSOLE.print(Panel(
        message,
        title="[bold green]WS[/]",
        border_style="green",
        box=box.ROUNDED,
    ))


def _debug_error(message: str) -> None:
    if _CONSOLE is None:
        return
    _CONSOLE.print(Panel(
        message,
        title="[bold red]Error[/]",
        border_style="red",
        box=box.ROUNDED,
    ))


def _debug_info(message: str) -> None:
    if _CONSOLE is None:
        return
    _CONSOLE.print(Panel(
        message,
        title="[bold blue]obs_bridge[/]",
        border_style="blue",
        box=box.ROUNDED,
    ))
