from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
except ImportError:
    Console = None
    Panel = None
    box = None


_CONSOLE = Console(stderr=True) if Console is not None else None


class DebugRecorder:
    def __init__(self, *, enabled: bool, root_dir: Path, write_latest_state: bool) -> None:
        self.enabled = enabled
        self.root_dir = root_dir
        self.write_latest_state = write_latest_state
        self._lock = threading.Lock()
        if self.enabled:
            self.root_dir.mkdir(parents=True, exist_ok=True)

    def write_event(self, *, kind: str, payload: dict[str, Any], state: dict[str, Any]) -> None:
        if not self.enabled:
            return
        timestamp = int(__import__("time").time() * 1000)
        path = self.root_dir / f"{timestamp}_{kind}.json"
        blob = {
            "payload": payload,
            "state": state,
        }
        with self._lock:
            path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.write_latest_state:
                (self.root_dir / "latest_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_state(self, state: dict[str, Any]) -> None:
        if not self.enabled or not self.write_latest_state:
            return
        with self._lock:
            (self.root_dir / "latest_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def panel_command(title: str, lines: list[str]) -> None:
    _print_panel(title=title, lines=lines, border_style="magenta")


def panel_speech(title: str, lines: list[str]) -> None:
    _print_panel(title=title, lines=lines, border_style="cyan")


def panel_backend(title: str, lines: list[str]) -> None:
    _print_panel(title=title, lines=lines, border_style="green")


def panel_idle(title: str, lines: list[str]) -> None:
    _print_panel(title=title, lines=lines, border_style="yellow")


def panel_info(title: str, lines: list[str]) -> None:
    _print_panel(title=title, lines=lines, border_style="blue")


def panel_error(title: str, lines: list[str]) -> None:
    _print_panel(title=title, lines=lines, border_style="red")


def _print_panel(*, title: str, lines: list[str], border_style: str) -> None:
    if _CONSOLE is None or Panel is None:
        return
    _CONSOLE.print(
        Panel(
            "\n".join(lines),
            title=title,
            border_style=border_style,
            box=box.ROUNDED if box is not None else None,
        )
    )
