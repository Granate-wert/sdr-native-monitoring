"""Immutable public shapes consumed by the UI V2 spectrum scene."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

import numpy as np

from ..i18n import UiLocale, text

class TraceKind(StrEnum):
    """Visible analytical trace roles; their data is already computed upstream."""

    CURRENT = "current"
    AVERAGE = "average"
    MAXIMUM = "maximum"
    MINIMUM = "minimum"


class VerticalRangeMode(StrEnum):
    """Presentation-only vertical scale policy."""

    AUTO = "auto"
    MANUAL = "manual"
    LOCKED = "locked"


@dataclass(frozen=True, slots=True)
class BandMask:
    """One presentation-only frequency region supplied by an external plan."""

    start_hz: float
    stop_hz: float
    label: str
    color: str = "#4DA3FF"

    def __post_init__(self) -> None:
        if not isfinite(self.start_hz) or not isfinite(self.stop_hz) or self.start_hz >= self.stop_hz:
            raise ValueError("band mask requires a finite ascending frequency interval")
        if not self.label.strip() or not self.color.strip():
            raise ValueError("band mask requires a label and a color")


@dataclass(frozen=True, slots=True)
class SpectrumFrameView:
    """Read-only view over one public spectrum frame; source arrays are not copied."""

    source_frame: object
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit_label: str

    @property
    def point_count(self) -> int:
        return int(self.values.size)


@dataclass(frozen=True, slots=True)
class EnvelopeTrace:
    """Bounded paint data retaining extrema and explicit NaN discontinuities."""

    frequencies_hz: np.ndarray
    values: np.ndarray
    source_point_count: int
    peak_preserving: bool

    @property
    def display_point_count(self) -> int:
        return int(self.values.size)


@dataclass(frozen=True, slots=True)
class SpectrumMarker:
    """One presentation marker resolved against the latest immutable frame."""

    marker_id: str
    frequency_hz: float
    value: float
    unit_label: str

    @property
    def label(self) -> str:
        return f"{self.marker_id}: {format_frequency_hz(self.frequency_hz)}, {self.value:.2f} {self.unit_label}"


def adapt_spectrum_frame(frame: object) -> SpectrumFrameView:
    """Validate a public frame shape without importing or changing domain code."""

    frequencies = np.asarray(getattr(frame, "frequencies_hz", None)).reshape(-1)
    values = np.asarray(getattr(frame, "values", None)).reshape(-1)
    unit_label = str(getattr(frame, "unit", "")).strip()
    if not unit_label:
        raise ValueError("spectrum frame must declare an exact non-empty unit")
    if frequencies.size == 0 or frequencies.size != values.size:
        raise ValueError("spectrum frame requires equally sized non-empty arrays")
    if not np.issubdtype(frequencies.dtype, np.number) or not np.issubdtype(values.dtype, np.number):
        raise TypeError("spectrum frame arrays must be numeric")
    if not np.all(np.isfinite(frequencies)):
        raise ValueError("spectrum frame frequency grid must be finite")
    if np.any(np.diff(frequencies) <= 0.0):
        raise ValueError("spectrum frame frequency grid must be strictly increasing")
    return SpectrumFrameView(
        source_frame=frame,
        frequencies_hz=frequencies,
        values=values,
        unit_label=unit_label,
    )


def format_frequency_hz(value_hz: float, *, locale: UiLocale = UiLocale.RU,
                        resolution_hz: float | None = None) -> str:
    """Format frequency axes and markers without changing the measured value."""

    magnitude = abs(float(value_hz))
    for divisor, key in (
        (1_000_000_000.0, "frequency.gigahertz"),
        (1_000_000.0, "frequency.megahertz"),
        (1_000.0, "frequency.kilohertz"),
    ):
        if magnitude >= divisor:
            if resolution_hz is not None and np.isfinite(resolution_hz) and resolution_hz > 0:
                decimals = max(3, min(12, int(np.ceil(np.log10(divisor / resolution_hz)))))
                return text(key + ".precise", locale, value=f"{value_hz / divisor:.{decimals}f}")
            return text(key, locale, value=value_hz / divisor)
    return text("frequency.hertz", locale, value=value_hz)
