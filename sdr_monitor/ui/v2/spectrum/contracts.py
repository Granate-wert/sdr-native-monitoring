"""Immutable public shapes consumed by the UI V2 spectrum scene."""

from __future__ import annotations

from dataclasses import dataclass, is_dataclass
from enum import StrEnum
from math import isfinite

import numpy as np

from ..i18n import UiLocale, text
from .cancellation import CancelCheck, check_cancelled
from .grid_baseline import MeasurementGridCache

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


@dataclass(frozen=True, slots=True, init=False)
class PreparedSpectrumFrame:
    """Validated immutable view; no Qt/viewport work or copied source arrays.

    Only frozen publications with read-only arrays may reuse validation on
    GUI delivery. Source identity remains available for exact markers.
    """

    view: SpectrumFrameView
    finite_extent: tuple[float, float] | None
    measurement_grid: np.ndarray | None

    def __init__(self, frame: object, *, grid_cache: MeasurementGridCache | None = None,
                 cancelled: CancelCheck = None) -> None:
        if not is_dataclass(frame) or not getattr(getattr(frame, "__dataclass_params__", None), "frozen", False):
            raise ValueError("prepared spectrum requires a frozen publication")
        check_cancelled(cancelled)
        view = adapt_spectrum_frame(frame)
        check_cancelled(cancelled)
        if view.frequencies_hz.flags.writeable or view.values.flags.writeable:
            raise ValueError("prepared spectrum arrays must be read-only")
        object.__setattr__(self, "view", view)
        object.__setattr__(self, "finite_extent", finite_value_extent(view.values, cancelled=cancelled))
        check_cancelled(cancelled)
        object.__setattr__(self, "measurement_grid", None if grid_cache is None else
                           grid_cache.prepare(view.frequencies_hz))
        check_cancelled(cancelled)


def finite_value_extent(values: np.ndarray, *, cancelled: CancelCheck = None) -> tuple[float, float] | None:
    """Exact full-frame Auto-Y extrema with bounded scratch, not a detector.

    NaN and either infinity keep their old presentation semantics: they do
    not determine Auto Y. No value is replaced in the original spectrum.
    Chunking bounds the temporary mask and gathered finite values to 64K
    elements, independently of the analytical grid size.
    """
    extent: tuple[float, float] | None = None
    for start in range(0, values.size, 65536):
        check_cancelled(cancelled)
        chunk = values[start:start + 65536]
        finite = chunk[np.isfinite(chunk)]
        if finite.size:
            low, high = float(np.min(finite)), float(np.max(finite))
            extent = (low, high) if extent is None else (min(extent[0], low), max(extent[1], high))
    return extent


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
    _validate_frequency_grid(frequencies)
    return SpectrumFrameView(
        source_frame=frame,
        frequencies_hz=frequencies,
        values=values,
        unit_label=unit_label,
    )


def _validate_frequency_grid(frequencies: np.ndarray) -> None:
    """Check every interval with <=64K difference scratch, including seams.

    Keep the former dtype-specific np.diff semantics and finite-error priority:
    a later non-finite bin wins over an earlier non-ascending interval. Do not
    trust source identity or cache a borrowed array's validity.
    """
    ascending = True
    for first in range(0, frequencies.size, 65536):
        chunk = frequencies[first:first + 65537]
        if not np.all(np.isfinite(chunk)):
            raise ValueError("spectrum frame frequency grid must be finite")
        if ascending and np.any(np.diff(chunk) <= 0.0):
            ascending = False
    if not ascending:
        raise ValueError("spectrum frame frequency grid must be strictly increasing")


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
