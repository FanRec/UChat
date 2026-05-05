from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ObsBridgeConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceSection:
    service_name: str
    listen_host: str
    listen_port: int
    mock_mode: bool


@dataclass(frozen=True)
class OverlayBreakpointConfig:
    max_width: int
    container_width: str
    container_max_width: str
    container_bottom: str
    container_gap: str
    line_padding: str
    font_size: str
    line_height: float
    border_radius: str
    text_stroke_width: str

    def css_variables(self) -> dict[str, str]:
        return {
            "--overlay-container-width": self.container_width,
            "--overlay-container-max-width": self.container_max_width,
            "--overlay-container-bottom": self.container_bottom,
            "--overlay-container-gap": self.container_gap,
            "--overlay-line-padding": self.line_padding,
            "--overlay-font-size": self.font_size,
            "--overlay-line-height": _css_number(self.line_height),
            "--overlay-line-radius": self.border_radius,
            "--overlay-text-stroke-width": self.text_stroke_width,
        }

    def to_frontend_payload(self) -> dict[str, Any]:
        return {
            "max_width": self.max_width,
            "css_variables": self.css_variables(),
        }


@dataclass(frozen=True)
class OverlayConfig:
    max_lines: int
    anchor: str
    font_family: str
    container_bottom: str
    container_top: str
    container_side_offset: str
    container_width: str
    container_max_width: str
    container_gap: str
    line_min_height: str
    line_padding: str
    font_size: str
    line_height: float
    font_weight: int
    letter_spacing: str
    text_align: str
    text_color: str
    active_text_color: str
    history_text_color: str
    line_background: str
    active_background: str
    history_background: str
    text_shadow: str
    border_radius: str
    text_stroke_width: str
    text_stroke_color: str
    box_shadow: str
    glow_enabled: bool
    glow_bottom: str
    glow_width: str
    glow_max_width: str
    glow_height: str
    glow_blur: str
    glow_background: str
    breakpoints: tuple[OverlayBreakpointConfig, ...]

    def css_variables(self) -> dict[str, str]:
        return {
            "--overlay-font-family": self.font_family,
            "--overlay-container-bottom": self.container_bottom,
            "--overlay-container-top": self.container_top,
            "--overlay-container-side-offset": self.container_side_offset,
            "--overlay-container-width": self.container_width,
            "--overlay-container-max-width": self.container_max_width,
            "--overlay-container-gap": self.container_gap,
            "--overlay-line-min-height": self.line_min_height,
            "--overlay-line-padding": self.line_padding,
            "--overlay-font-size": self.font_size,
            "--overlay-line-height": _css_number(self.line_height),
            "--overlay-font-weight": str(self.font_weight),
            "--overlay-letter-spacing": self.letter_spacing,
            "--overlay-text-align": self.text_align,
            "--overlay-line-color": self.text_color,
            "--overlay-active-color": self.active_text_color,
            "--overlay-history-color": self.history_text_color,
            "--overlay-line-background": self.line_background,
            "--overlay-active-background": self.active_background,
            "--overlay-history-background": self.history_background,
            "--overlay-text-shadow": self.text_shadow,
            "--overlay-line-radius": self.border_radius,
            "--overlay-text-stroke-width": self.text_stroke_width,
            "--overlay-text-stroke-color": self.text_stroke_color,
            "--overlay-box-shadow": self.box_shadow,
            "--overlay-glow-bottom": self.glow_bottom,
            "--overlay-glow-width": self.glow_width,
            "--overlay-glow-max-width": self.glow_max_width,
            "--overlay-glow-height": self.glow_height,
            "--overlay-glow-blur": self.glow_blur,
            "--overlay-glow-background": self.glow_background,
        }

    def to_frontend_payload(self) -> dict[str, Any]:
        return {
            "max_lines": self.max_lines,
            "anchor": self.anchor,
            "glow_enabled": self.glow_enabled,
            "css_variables": self.css_variables(),
            "breakpoints": [item.to_frontend_payload() for item in self.breakpoints],
        }


@dataclass(frozen=True)
class ObservabilityConfig:
    log_level: str
    debug_dump_enabled: bool
    debug_dump_dir: str


@dataclass(frozen=True)
class ObsBridgeConfig:
    service: ServiceSection
    overlay: OverlayConfig
    observability: ObservabilityConfig

    @classmethod
    def load(cls, path: str | Path | None = None) -> ObsBridgeConfig:
        config_path = Path(path) if path is not None else Path(__file__).resolve().parent / "config" / "service.toml"
        if not config_path.exists():
            raise ObsBridgeConfigError(f"obs_bridge config file not found: {config_path}")
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
        service_raw = _section(raw, "service", config_path)
        overlay_raw = _section(raw, "overlay", config_path)
        obs_raw = _section(raw, "observability", config_path)
        glow_raw = _mapping_default(overlay_raw, "glow")
        breakpoints_raw = _mapping_default(overlay_raw, "breakpoints")
        compact_raw = _mapping_default(breakpoints_raw, "compact")
        mobile_raw = _mapping_default(breakpoints_raw, "mobile")
        return cls(
            service=ServiceSection(
                service_name=_str_default(service_raw, "service_name", "obs_bridge"),
                listen_host=_str_default(service_raw, "listen_host", "127.0.0.1"),
                listen_port=_int_default(service_raw, "listen_port", 8104),
                mock_mode=_bool_default(service_raw, "mock_mode", False),
            ),
            overlay=OverlayConfig(
                max_lines=_int_default(overlay_raw, "max_lines", 5),
                anchor=_str_default(overlay_raw, "anchor", "bottom_center"),
                font_family=_str_default(overlay_raw, "font_family", '"Nunito", "Trebuchet MS", "Segoe UI", sans-serif'),
                container_bottom=_str_default(overlay_raw, "container_bottom", "32px"),
                container_top=_str_default(overlay_raw, "container_top", "32px"),
                container_side_offset=_str_default(overlay_raw, "container_side_offset", "32px"),
                container_width=_str_default(overlay_raw, "container_width", "72vw"),
                container_max_width=_str_default(overlay_raw, "container_max_width", "760px"),
                container_gap=_str_default(overlay_raw, "container_gap", "6px"),
                line_min_height=_str_default(overlay_raw, "line_min_height", "1.4em"),
                line_padding=_str_default(overlay_raw, "line_padding", "3px 12px"),
                font_size=_str_default(overlay_raw, "font_size", "29px"),
                line_height=_float_default(overlay_raw, "line_height", 1.34),
                font_weight=_int_default(overlay_raw, "font_weight", 700),
                letter_spacing=_str_default(overlay_raw, "letter_spacing", "0.01em"),
                text_align=_str_default(overlay_raw, "text_align", "center"),
                text_color=_str_default(overlay_raw, "text_color", "#fffdfd"),
                active_text_color=_str_default(overlay_raw, "active_text_color", "#fffdfd"),
                history_text_color=_str_default(overlay_raw, "history_text_color", "#fffdfd"),
                line_background=_str_default(overlay_raw, "line_background", "rgba(24, 26, 34, 0.14)"),
                active_background=_str_default(overlay_raw, "active_background", "rgba(24, 26, 34, 0.14)"),
                history_background=_str_default(overlay_raw, "history_background", "rgba(24, 26, 34, 0.14)"),
                text_shadow=_str_default(
                    overlay_raw,
                    "text_shadow",
                    "0 2px 0 rgba(31, 21, 39, 0.7), 0 3px 12px rgba(0, 0, 0, 0.26)",
                ),
                border_radius=_str_default(overlay_raw, "border_radius", "14px"),
                text_stroke_width=_str_default(overlay_raw, "text_stroke_width", "4px"),
                text_stroke_color=_str_default(overlay_raw, "text_stroke_color", "rgba(31, 21, 39, 0.92)"),
                box_shadow=_str_default(
                    overlay_raw,
                    "box_shadow",
                    "0 1px 0 rgba(255, 255, 255, 0.16) inset, 0 10px 24px rgba(20, 18, 30, 0.1)",
                ),
                glow_enabled=_bool_default(glow_raw, "enabled", True),
                glow_bottom=_str_default(glow_raw, "bottom", "10px"),
                glow_width=_str_default(glow_raw, "width", "70vw"),
                glow_max_width=_str_default(glow_raw, "max_width", "760px"),
                glow_height=_str_default(glow_raw, "height", "150px"),
                glow_blur=_str_default(glow_raw, "blur", "12px"),
                glow_background=_str_default(
                    glow_raw,
                    "background",
                    "radial-gradient(circle at center, rgba(255, 212, 236, 0.12), rgba(255, 212, 236, 0))",
                ),
                breakpoints=(
                    OverlayBreakpointConfig(
                        max_width=_int_default(compact_raw, "max_width", 1280),
                        container_width=_str_default(compact_raw, "container_width", "80vw"),
                        container_max_width=_str_default(compact_raw, "container_max_width", "720px"),
                        container_bottom=_str_default(compact_raw, "container_bottom", "26px"),
                        container_gap=_str_default(compact_raw, "container_gap", "6px"),
                        line_padding=_str_default(compact_raw, "line_padding", "3px 12px"),
                        font_size=_str_default(compact_raw, "font_size", "25px"),
                        line_height=_float_default(compact_raw, "line_height", 1.34),
                        border_radius=_str_default(compact_raw, "border_radius", "14px"),
                        text_stroke_width=_str_default(compact_raw, "text_stroke_width", "3px"),
                    ),
                    OverlayBreakpointConfig(
                        max_width=_int_default(mobile_raw, "max_width", 768),
                        container_width=_str_default(mobile_raw, "container_width", "90vw"),
                        container_max_width=_str_default(mobile_raw, "container_max_width", "640px"),
                        container_bottom=_str_default(mobile_raw, "container_bottom", "18px"),
                        container_gap=_str_default(mobile_raw, "container_gap", "4px"),
                        line_padding=_str_default(mobile_raw, "line_padding", "2px 9px"),
                        font_size=_str_default(mobile_raw, "font_size", "20px"),
                        line_height=_float_default(mobile_raw, "line_height", 1.28),
                        border_radius=_str_default(mobile_raw, "border_radius", "10px"),
                        text_stroke_width=_str_default(mobile_raw, "text_stroke_width", "2.6px"),
                    ),
                ),
            ),
            observability=ObservabilityConfig(
                log_level=_str_default(obs_raw, "log_level", "INFO"),
                debug_dump_enabled=_bool_default(obs_raw, "debug_dump_enabled", False),
                debug_dump_dir=_str_default(obs_raw, "debug_dump_dir", "debug/obs_bridge"),
            ),
        )


def _section(data: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ObsBridgeConfigError(f"missing section [{key}] in {path}")
    return value


def _mapping_default(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, Mapping):
        raise ObsBridgeConfigError(f"invalid table key '{key}'")
    return value


def _str_default(data: Mapping[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ObsBridgeConfigError(f"invalid string key '{key}'")
    return value


def _int_default(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int):
        raise ObsBridgeConfigError(f"invalid integer key '{key}'")
    return value


def _float_default(data: Mapping[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if not isinstance(value, (int, float)):
        raise ObsBridgeConfigError(f"invalid float key '{key}'")
    return float(value)


def _bool_default(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ObsBridgeConfigError(f"invalid boolean key '{key}'")
    return value


def _css_number(value: float) -> str:
    return f"{value:g}"
