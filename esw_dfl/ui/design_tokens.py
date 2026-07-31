"""Central design tokens; scientific palettes stay outside UI themes."""

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
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"


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
    body: str = "10pt"
    body_compact: str = "9pt"
    label: str = "9pt"
    heading: str = "11pt"
    numeric: str = "10pt"
    mono: str = "Consolas"


@dataclass(frozen=True, slots=True)
class SemanticColors:
    surface: str
    panel: str
    border: str
    text: str
    primary: str
    success: str
    warning: str
    error: str
    info: str
    muted: str


DARK_COLORS = SemanticColors(
    surface="#171c24",
    panel="#202832",
    border="#30363d",
    text="#e6edf3",
    primary="#1f6feb",
    success="#3ddc97",
    warning="#ffbd2e",
    error="#ff5f56",
    info="#35c6ff",
    muted="#8b949e",
)
LIGHT_COLORS = SemanticColors(
    surface="#f6f8fa",
    panel="#ffffff",
    border="#d0d7de",
    text="#1f2328",
    primary="#0969da",
    success="#1a7f37",
    warning="#9a6700",
    error="#cf222e",
    info="#0550ae",
    muted="#57606a",
)
HIGH_CONTRAST_COLORS = SemanticColors(
    surface="#000000",
    panel="#000000",
    border="#ffffff",
    text="#ffffff",
    primary="#ffff00",
    success="#00ff00",
    warning="#ffff00",
    error="#ff5f56",
    info="#00ffff",
    muted="#ffffff",
)

# These are deliberately separate from SemanticColors and must never be
# substituted for scientific/measurement palettes in renderers.
COLOR_BLIND_SCIENTIFIC_PALETTES: dict[str, tuple[str, ...]] = {
    "cividis": ("#00224e", "#34456c", "#6c6f6f", "#a59c74", "#fee838"),
    "viridis": ("#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"),
    "okabe_ito": ("#000000", "#e69f00", "#56b4e9", "#009e73", "#f0e442", "#0072b2", "#d55e00"),
}


@dataclass(frozen=True, slots=True)
class DesignTokens:
    spacing: Spacing = Spacing()
    radius: Radius = Radius()
    typography: Typography = Typography()
