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
class OverlayConfig:
    display_duration: float
    typewriter_speed_ms: int
    turn_end_fade_delay: float
    fade_in_ms: int
    fade_out_ms: int
    max_lines: int
    font_size: str
    font_color: str
    active_color: str
    dim_color: str
    text_shadow: str
    background: str
    border_radius: str
    padding: str
    position: str
    bottom_offset: str
    max_width: str
    line_spacing: str
    style_preset: str


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
        return cls(
            service=ServiceSection(
                service_name=_str_default(service_raw, "service_name", "obs_bridge"),
                listen_host=_str_default(service_raw, "listen_host", "127.0.0.1"),
                listen_port=_int_default(service_raw, "listen_port", 8104),
                mock_mode=_bool_default(service_raw, "mock_mode", False),
            ),
            overlay=OverlayConfig(
                display_duration=_float_default(overlay_raw, "display_duration", 8.0),
                typewriter_speed_ms=_int_default(overlay_raw, "typewriter_speed_ms", 35),
                turn_end_fade_delay=_float_default(overlay_raw, "turn_end_fade_delay", 4.0),
                fade_in_ms=_int_default(overlay_raw, "fade_in_ms", 250),
                fade_out_ms=_int_default(overlay_raw, "fade_out_ms", 500),
                max_lines=_int_default(overlay_raw, "max_lines", 5),
                font_size=_str_default(overlay_raw, "font_size", "26px"),
                font_color=_str_default(overlay_raw, "font_color", "#FFFFFF"),
                active_color=_str_default(overlay_raw, "active_color", "#FFD700"),
                dim_color=_str_default(overlay_raw, "dim_color", "rgba(255,255,255,0.45)"),
                text_shadow=_str_default(overlay_raw, "text_shadow", "1px 1px 3px rgba(0,0,0,0.9)"),
                background=_str_default(overlay_raw, "background", "rgba(0,0,0,0.5)"),
                border_radius=_str_default(overlay_raw, "border_radius", "10px"),
                padding=_str_default(overlay_raw, "padding", "10px 18px"),
                position=_str_default(overlay_raw, "position", "bottom_center"),
                bottom_offset=_str_default(overlay_raw, "bottom_offset", "60px"),
                max_width=_str_default(overlay_raw, "max_width", "750px"),
                line_spacing=_str_default(overlay_raw, "line_spacing", "6px"),
                style_preset=_str_default(overlay_raw, "style_preset", "bubble"),
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
