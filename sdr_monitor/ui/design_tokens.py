"""Semantic tokens for the standalone SDR Native Monitoring interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ThemeId(StrEnum):
    SYSTEM = "system"
    DARK = "dark"
    LIGHT = "light"
    HIGH_CONTRAST = "high_contrast"


class StatusTone(StrEnum):
    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Spacing:
    XS: int = 4
    S: int = 8
    M: int = 12
    L: int = 16
    XL: int = 24


@dataclass(frozen=True, slots=True)
class Radius:
    S: int = 3
    M: int = 6


@dataclass(frozen=True, slots=True)
class Typography:
    title: str = "18px"
    workspace_title: str = "20px"
    heading: str = "14px"
    body: str = "13px"
    compact: str = "12px"
    status: str = "11px"
    numeric_family: str = "Cascadia Mono"


@dataclass(frozen=True, slots=True)
class SemanticColors:
    surface: str
    panel: str
    elevated: str
    border: str
    text: str
    secondary_text: str
    muted: str
    accent: str
    success: str
    warning: str
    error: str
    info: str


DARK_COLORS = SemanticColors(
    surface="#0F141A", panel="#171E26", elevated="#1E2731", border="#2D3945",
    text="#E8EEF5", secondary_text="#9EABB8", muted="#6F7D89", accent="#3CA6FF",
    success="#45C486", warning="#F2B84B", error="#F06464", info="#6F8FFF",
)
LIGHT_COLORS = SemanticColors(
    surface="#F5F7FA", panel="#FFFFFF", elevated="#E9EEF4", border="#B9C5D0",
    text="#15202B", secondary_text="#425466", muted="#627282", accent="#0969DA",
    success="#1A7F37", warning="#8A5600", error="#B42318", info="#175CD3",
)
HIGH_CONTRAST_COLORS = SemanticColors(
    surface="#000000", panel="#000000", elevated="#101010", border="#FFFFFF",
    text="#FFFFFF", secondary_text="#FFFFFF", muted="#FFFFFF", accent="#FFFF00",
    success="#00FF00", warning="#FFFF00", error="#FF7777", info="#00FFFF",
)

# Scientific colours are deliberately not UI-theme colours.
SCIENTIFIC_COLORS = {
    "current_spectrum": "#55D6BE", "average": "#4DA3FF", "max_hold": "#FFB454",
    "min_hold": "#A78BFA", "marker": "#FFD166", "channel_region": "#50C878",
}
COLOR_BLIND_SCIENTIFIC_PALETTES = {
    "cividis": ("#00224E", "#34456C", "#6C6F6F", "#A59C74", "#FEE838"),
    "viridis": ("#440154", "#3B528B", "#21918C", "#5EC962", "#FDE725"),
    "okabe_ito": ("#000000", "#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00"),
}


@dataclass(frozen=True, slots=True)
class DesignTokens:
    spacing: Spacing = Spacing()
    radius: Radius = Radius()
    typography: Typography = Typography()


def relative_luminance(hex_color: str) -> float:
    """Return WCAG relative luminance for a six-digit colour."""
    channels = tuple(int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    linear = tuple(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    high, low = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)
