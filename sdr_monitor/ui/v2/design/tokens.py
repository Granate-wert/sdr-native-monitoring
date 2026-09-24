"""Immutable semantic visual tokens for UI V2.

Scientific trace colours are deliberately stable across shell themes. They
describe measurement layers, not incidental controls, and must stay separate
from neutral/status colours.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class ThemeId(StrEnum):
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
class SpacingTokens:
    tight: int = 4
    control: int = 8
    compact_panel: int = 12
    panel: int = 16
    section: int = 24


@dataclass(frozen=True, slots=True)
class RadiusTokens:
    control: int = 6
    card: int = 8
    popover: int = 10


@dataclass(frozen=True, slots=True)
class TypographyTokens:
    workspace_title_px: int = 20
    section_title_px: int = 14
    body_px: int = 13
    secondary_px: int = 12
    status_px: int = 11
    measurement_px: int = 20
    body_family: str = "Segoe UI"
    numeric_family: str = "Consolas"


@dataclass(frozen=True, slots=True)
class SemanticColors:
    background: str
    panel: str
    elevated: str
    control: str
    border: str
    primary_text: str
    secondary_text: str
    muted_text: str
    accent: str
    accent_hover: str
    accent_pressed: str
    focus_ring: str
    success: str
    warning: str
    error: str
    info: str

    def status(self, tone: StatusTone) -> str:
        return {
            StatusTone.NEUTRAL: self.secondary_text,
            StatusTone.INFO: self.info,
            StatusTone.SUCCESS: self.success,
            StatusTone.WARNING: self.warning,
            StatusTone.ERROR: self.error,
        }[tone]


@dataclass(frozen=True, slots=True)
class ScientificColors:
    current_spectrum: str = "#55D6BE"
    average: str = "#4DA3FF"
    max_hold: str = "#FFB454"
    min_hold: str = "#A78BFA"
    previous_sweep: str = "#9EABB8"
    marker: str = "#FFD166"


@dataclass(frozen=True, slots=True)
class DesignTokens:
    theme: ThemeId
    colors: SemanticColors
    spacing: SpacingTokens = SpacingTokens()
    radius: RadiusTokens = RadiusTokens()
    typography: TypographyTokens = TypographyTokens()
    scientific: ScientificColors = ScientificColors()


_DARK = SemanticColors(
    background="#0F141A",
    panel="#171E26",
    elevated="#1E2731",
    control="#25313D",
    border="#2D3945",
    primary_text="#E8EEF5",
    secondary_text="#9EABB8",
    muted_text="#6F7D89",
    accent="#3CA6FF",
    accent_hover="#6AB9FF",
    accent_pressed="#168BE7",
    focus_ring="#78C1FF",
    success="#53D18B",
    warning="#FFB454",
    error="#FF6B6B",
    info="#6F8FFF",
)

_LIGHT = SemanticColors(
    background="#F5F7FA",
    panel="#FFFFFF",
    elevated="#E9EEF4",
    control="#FFFFFF",
    border="#B9C5D0",
    primary_text="#15202B",
    secondary_text="#425466",
    muted_text="#627282",
    accent="#0969DA",
    accent_hover="#0759BC",
    accent_pressed="#054DA3",
    focus_ring="#005CC5",
    success="#1A7F37",
    warning="#8A5600",
    error="#B42318",
    info="#175CD3",
)

_HIGH_CONTRAST = SemanticColors(
    background="#000000",
    panel="#000000",
    elevated="#101010",
    control="#000000",
    border="#FFFFFF",
    primary_text="#FFFFFF",
    secondary_text="#FFFFFF",
    muted_text="#FFFFFF",
    accent="#FFFF00",
    accent_hover="#FFFF00",
    accent_pressed="#D8D800",
    focus_ring="#00FFFF",
    success="#00FF00",
    warning="#FFFF00",
    error="#FF7777",
    info="#00FFFF",
)

_SCIENTIFIC = {
    ThemeId.DARK: ScientificColors(),
    ThemeId.LIGHT: ScientificColors(
        current_spectrum="#006B5F", average="#005FCC", max_hold="#8A4B00",
        min_hold="#6B3FA0", previous_sweep="#425466", marker="#7A5500",
    ),
    ThemeId.HIGH_CONTRAST: ScientificColors(
        current_spectrum="#00FFFF", average="#FFFFFF", max_hold="#FFFF00",
        min_hold="#FF66FF", previous_sweep="#9DADFF", marker="#FF9900",
    ),
}


def tokens_for_theme(theme: ThemeId | str) -> DesignTokens:
    """Resolve one supported theme, falling back deterministically to dark."""

    try:
        selected = ThemeId(theme)
    except ValueError:
        selected = ThemeId.DARK
    colors = {
        ThemeId.DARK: _DARK,
        ThemeId.LIGHT: _LIGHT,
        ThemeId.HIGH_CONTRAST: _HIGH_CONTRAST,
    }[selected]
    return DesignTokens(theme=selected, colors=colors, scientific=_SCIENTIFIC[selected])


def density_lookup_table() -> np.ndarray:
    """Authoritative Inferno-like RGBA lookup table shared by image and legend."""
    stops = np.array(((0, 0, 4, 0), (87, 15, 109, 150), (187, 55, 84, 205),
                      (249, 142, 8, 235), (252, 255, 164, 255)), dtype=np.float32)
    positions = np.linspace(0.0, 1.0, stops.shape[0])
    output: np.ndarray = np.empty((256, 4), dtype=np.ubyte)
    target = np.linspace(0.0, 1.0, output.shape[0])
    for channel in range(4):
        output[:, channel] = np.interp(target, positions, stops[:, channel]).astype(np.ubyte)
    output.setflags(write=False)
    return output


def relative_luminance(color: str) -> float:
    """Return WCAG relative luminance for a six-digit hexadecimal colour."""

    if len(color) != 7 or not color.startswith("#"):
        raise ValueError("colour must be #RRGGBB")
    channels = tuple(int(color[index : index + 2], 16) / 255.0 for index in (1, 3, 5))
    linear = tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio for two semantic token colours."""

    light, dark = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)
