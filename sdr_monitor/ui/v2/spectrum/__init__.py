"""Measurement-only spectrum scene primitives for UI V2."""

from .contracts import (
    BandMask,
    EnvelopeTrace,
    SpectrumFrameView,
    SpectrumMarker,
    TraceKind,
    VerticalRangeMode,
    adapt_spectrum_frame,
)
from .envelope import peak_preserving_envelope
from .persistence_contracts import DensityValueMode, PersistenceDensityFrame, PersistenceRenderMode
from .persistence_overlay import PersistenceOverlayMetrics
from .scene import SpectrumScene

__all__ = [
    "EnvelopeTrace",
    "DensityValueMode",
    "PersistenceDensityFrame",
    "PersistenceOverlayMetrics",
    "PersistenceRenderMode",
    "BandMask",
    "SpectrumFrameView",
    "SpectrumMarker",
    "SpectrumScene",
    "TraceKind",
    "VerticalRangeMode",
    "adapt_spectrum_frame",
    "peak_preserving_envelope",
]
