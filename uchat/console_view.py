from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.console import Group

from uchat.config import DebugConfig
from uchat.models import LLMStreamEvent


class MessageChainConsoleRenderer:
    def __init__(self, config: DebugConfig, *, console: Console | None = None):
        self.config = config
        self.console = console or Console()

    def render(self, message_chain: dict[str, Any]) -> None:
        self.render_turn_start(message_chain)
        stages = message_chain.get("stages", [])
        output_index = _output_stage_index(stages)
        for stage in stages:
            self.render_stage(stage, output_index=output_index)

    def render_turn_start(self, message_chain: dict[str, Any]) -> None:
        if self.config.console_view == "conversation":
            self.console.print(self._conversation_user_panel(message_chain))
            return
        self.console.print(self._overview_panel(message_chain))

    def render_stage(self, stage: dict[str, Any], *, live: bool = False, output_index: int | None = None) -> None:
        if self.config.console_view == "conversation":
            status = str(stage.get("status", "")).lower()
            stage_name = str(stage.get("stage", "")).lower()
            if status in {"degraded", "error", "recovered"} or (stage_name == "turn_complete" and status != "ok"):
                self.console.print(self._conversation_notice_panel(stage))
            return
        self.console.print(self._stage_panel(stage, output_index, live=live))

    def render_llm_stream_event(self, event: LLMStreamEvent, event_index: int) -> None:
        if self.config.console_view == "conversation":
            title = f"TTS CHUNK #{event_index:02d}"
            subtitle = f"chars={len(event.text)} | t={round(event.created_at_ms, 2)}ms"
            chunk_text = event.text
        else:
            title = f"TTS CHUNK #{event_index:02d}"
            subtitle = f"chars={len(event.text)} | t={round(event.created_at_ms, 2)}ms"
            chunk_text = event.text
        body = [
            Panel(
                Text(chunk_text or "", style="white"),
                title="chunk",
                border_style="yellow",
                box=box.SQUARE,
                expand=True,
            )
        ]
        self.console.print(
            Panel(
                Group(*body),
                title=title,
                subtitle=subtitle,
                border_style="yellow",
                box=box.ROUNDED,
                expand=True,
            )
        )

    def render_assistant_message(self, text: str) -> None:
        self.console.print(
            Panel(
                Text(text, style="white"),
                title="ASSISTANT",
                border_style="yellow",
                box=box.ROUNDED,
                expand=True,
            )
        )

    def _overview_panel(self, message_chain: dict[str, Any]) -> Panel:
        summary = Table.grid(expand=True)
        summary.add_column(style="bold cyan", width=10)
        summary.add_column(style="white")
        summary.add_row("Trace", str(message_chain.get("trace_id", "-")))
        summary.add_row("Session", str(message_chain.get("session_id", "-")))
        summary.add_row("Degraded", _bool_label(message_chain.get("degraded")))
        degraded_reason = message_chain.get("degraded_reason")
        if degraded_reason:
            summary.add_row("Reason", str(degraded_reason))
        summary.add_row("User", str(message_chain.get("user_input", "")))
        return Panel(
            summary,
            title="MESSAGE CHAIN TIMELINE",
            border_style="bright_blue",
            box=box.ROUNDED,
            expand=True,
        )

    def _conversation_user_panel(self, message_chain: dict[str, Any]) -> Panel:
        return Panel(
            Text(str(message_chain.get("user_input", "")), style="white"),
            title="USER",
            border_style="bright_blue",
            box=box.ROUNDED,
            expand=True,
        )

    def _conversation_notice_panel(self, stage: dict[str, Any]) -> Panel:
        lines = []
        if stage.get("error"):
            lines.append(str(stage["error"]))
        result = stage.get("result") or {}
        if result.get("summary"):
            lines.append(str(result["summary"]))
        if not lines:
            lines.append(f"{stage.get('stage', 'stage')}: {stage.get('status', '-')}")
        return Panel(
            Text("\n".join(lines), style="white"),
            title=str(stage.get("stage", "NOTICE")).upper(),
            border_style=_stage_border_style(stage),
            box=box.ROUNDED,
            expand=True,
        )

    def _stage_panel(self, stage: dict[str, Any], output_index: int | None, *, live: bool = False) -> Panel:
        body: list[Any] = [self._stage_meta_table(stage)]

        metrics = stage.get("metrics") or {}
        if metrics:
            body.append(self._dict_panel("metrics", metrics, border_style="bright_black"))

        result = stage.get("result") or {}
        summary = result.get("summary")
        if summary:
            body.append(
                Panel(
                    Text(str(summary), style="white"),
                    title="summary",
                    border_style=_summary_border_style(stage),
                    box=box.SQUARE,
                    expand=True,
                )
            )

        full = result.get("full")
        if self._should_show_full(stage, output_index, bool(full), live=live):
            body.append(
                Panel(
                    self._result_renderable(result),
                    title="result",
                    border_style=_stage_border_style(stage),
                    box=box.ROUNDED,
                    expand=True,
                )
            )

        error = stage.get("error")
        if error:
            body.append(
                Panel(
                    Text(str(error), style="bold red"),
                    title="error",
                    border_style="red",
                    box=box.SQUARE,
                    expand=True,
                )
            )

        title = f"{int(stage.get('index', 0)):02d} {str(stage.get('stage', 'stage')).upper()}"
        subtitle_parts = [str(stage.get("service", "-")), str(stage.get("status", "-"))]
        if stage.get("decision"):
            subtitle_parts.append(f"decision={stage['decision']}")
        return Panel(
            Group(*body),
            title=title,
            subtitle=" | ".join(subtitle_parts),
            border_style=_stage_border_style(stage),
            box=box.ROUNDED,
            expand=True,
        )

    def _stage_meta_table(self, stage: dict[str, Any]) -> Table:
        table = Table.grid(expand=True)
        table.add_column(style="bold")
        table.add_column(style="white")
        if stage.get("latency_ms") is not None:
            table.add_row("latency_ms", str(stage["latency_ms"]))
        if stage.get("fallback_source"):
            table.add_row("fallback", str(stage["fallback_source"]))
        table.add_row("degraded", _bool_label(stage.get("degraded")))
        return table

    def _dict_panel(self, title: str, data: dict[str, Any], *, border_style: str) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(style="bold")
        table.add_column(style="white")
        for key, value in data.items():
            if value is None:
                continue
            table.add_row(str(key), str(value))
        return Panel(table, title=title, border_style=border_style, box=box.SQUARE, expand=True)

    def _result_renderable(self, result: dict[str, Any]) -> Any:
        full = str(result.get("full", ""))
        mime = str(result.get("mime") or "")
        if mime == "application/json":
            return Syntax(full, "json", theme="ansi_dark", word_wrap=True, line_numbers=False)
        return Text(full, style="white")

    def _should_show_full(self, stage: dict[str, Any], output_index: int | None, has_full: bool, *, live: bool) -> bool:
        if not has_full:
            return False
        stage_name = str(stage.get("stage", ""))
        index = int(stage.get("index", 0))
        if stage_name in {"output", "output_dispatch"}:
            return True
        if live:
            if stage_name == "context_pack" and not self.config.show_memory_context_in_console:
                return False
            return self.config.show_full_pre_output_results
        if output_index is None or index >= output_index:
            return False
        if stage_name == "context_pack" and not self.config.show_memory_context_in_console:
            return False
        return self.config.show_full_pre_output_results

def truncate_text(value: str, limit: int) -> str:
    clean = value.strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(limit - 3, 0)] + "..."


def _bool_label(value: Any) -> str:
    if value is None:
        return "-"
    return "yes" if bool(value) else "no"


def _output_stage_index(stages: list[dict[str, Any]]) -> int | None:
    for stage in stages:
        if stage.get("stage") in {"output", "output_dispatch"}:
            return int(stage.get("index", 0))
    return None


def _stage_style(stage: dict[str, Any]) -> str:
    status = str(stage.get("status", "")).lower()
    if status == "error":
        return "bold red"
    if status == "degraded":
        return "yellow"
    if status == "recovered":
        return "green"
    service = str(stage.get("service", "")).lower()
    if service == "llm":
        return "magenta"
    if service == "ltmem":
        return "cyan"
    return "white"


def _stage_border_style(stage: dict[str, Any]) -> str:
    status = str(stage.get("status", "")).lower()
    if status == "error":
        return "red"
    if status == "degraded":
        return "yellow"
    if status == "recovered":
        return "green"
    stage_name = str(stage.get("stage", "")).lower()
    service = str(stage.get("service", "")).lower()
    if service == "llm" or stage_name in {"output", "output_dispatch"}:
        return "yellow"
    if service == "ltmem":
        return "cyan"
    if service == "tool":
        return "bright_green"
    return "blue"


def _summary_border_style(stage: dict[str, Any]) -> str:
    if str(stage.get("service", "")).lower() == "llm" or str(stage.get("stage", "")).lower() in {"output", "output_dispatch"}:
        return "yellow"
    return "bright_black"
