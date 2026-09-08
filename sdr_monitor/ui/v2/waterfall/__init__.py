"""Bounded, presentation-only waterfall primitives for UI V2."""

from .contracts import (
    WaterfallDirection,
    WaterfallDisplayConfig,
    WaterfallGridSignature,
    WaterfallLineFrame,
    WaterfallPalette,
    adapt_waterfall_line,
)
from .pane import WaterfallPane, WaterfallPaneMetrics
from .spectrum_view import SpectrumWaterfallView

__all__ = [
    "SpectrumWaterfallView",
    "WaterfallDirection",
    "WaterfallDisplayConfig",
    "WaterfallGridSignature",
    "WaterfallLineFrame",
    "WaterfallPalette",
    "WaterfallPane",
    "WaterfallPaneMetrics",
    "adapt_waterfall_line",
]
