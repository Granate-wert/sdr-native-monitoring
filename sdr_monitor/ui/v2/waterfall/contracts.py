"""Explicit immutable waterfall input and display configuration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import cast

import numpy as np

from .bounded_ring import DEFAULT_WATERFALL_PRESENTATION_BUDGET
from .sweep_rows import SweepRowStamp


class WaterfallDirection(StrEnum):
    """Position of the newest admitted presentation row."""

    NEWEST_AT_TOP = "newest_at_top"
    NEWEST_AT_BOTTOM = "newest_at_bottom"


class WaterfallPalette(StrEnum):
    """Display-only colour mappings; none changes analytical values."""

    VIRIDIS = "viridis"
    CIVIDIS = "cividis"
    TURBO = "turbo"
    GRAYSCALE = "grayscale"


@dataclass(frozen=True, slots=True)
class WaterfallGridSignature:
    """Complete regular-grid identity; mixed signatures always form a new epoch."""

    configuration_generation: int | str
    unit_label: str
    columns: int
    first_edge_hz: float
    last_edge_hz: float
    spacing_hz: float
    timestamp_known: bool


@dataclass(frozen=True, slots=True)
class WaterfallLineFrame:
    """One externally computed time/frequency row with physical bin edges.

    UI2-06 deliberately does not adapt a Live snapshot or infer bin geometry.
    UI2-07 may compose this explicit shape from an already published contract.
    """

    values: np.ndarray
    frequency_edges_hz: np.ndarray
    timestamp_ns: int
    configuration_generation: int | str
    unit_label: str
    timestamp_known: bool = True
    sequence: int | None = None

    def __post_init__(self) -> None:
        values = np.asarray(self.values)
        edges = np.asarray(self.frequency_edges_hz)
        if values.ndim != 1 or values.size == 0 or not np.issubdtype(values.dtype, np.number):
            raise ValueError("waterfall values must be a non-empty numeric one-dimensional array")
        if edges.ndim != 1 or edges.size != values.size + 1:
            raise ValueError("waterfall requires exactly one physical edge more than values")
        if not np.issubdtype(edges.dtype, np.number) or not np.all(np.isfinite(edges)):
            raise ValueError("waterfall frequency edges must be finite numeric values")
        spacing = np.diff(edges)
        if np.any(spacing <= 0.0):
            raise ValueError("waterfall frequency edges must be strictly increasing")
        if not np.allclose(spacing, spacing[0], rtol=1e-9, atol=1e-9):
            raise ValueError("waterfall display requires regular physical frequency bin edges")
        if not self.unit_label.strip():
            raise ValueError("waterfall unit must be explicit")
        _validate_generation(self.configuration_generation)
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int) or self.timestamp_ns < 0:
            raise ValueError("waterfall timestamp_ns must be a non-negative integer")
        if not isinstance(self.timestamp_known, bool):
            raise ValueError("waterfall timestamp provenance must be explicit")
        if self.sequence is not None and (isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0):
            raise ValueError("waterfall sequence must be a non-negative integer or absent")
        # Validate against the existing authoritative display budget before any
        # renderer allocation. The row itself remains source-owned and is not copied.
        DEFAULT_WATERFALL_PRESENTATION_BUDGET.estimate(1, int(values.size))
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "frequency_edges_hz", edges)

    @property
    def grid_signature(self) -> WaterfallGridSignature:
        spacing = float(self.frequency_edges_hz[1] - self.frequency_edges_hz[0])
        return WaterfallGridSignature(
            configuration_generation=self.configuration_generation,
            unit_label=self.unit_label.strip(),
            columns=int(self.values.size),
            first_edge_hz=float(self.frequency_edges_hz[0]),
            last_edge_hz=float(self.frequency_edges_hz[-1]),
            spacing_hz=spacing,
            timestamp_known=self.timestamp_known,
        )


@dataclass(frozen=True, slots=True)
class SweepWaterfallLine:
    """Explicit replaceable pass, using the shared physical-row renderer.

    Completion time is not RF acquisition time for a stitched sweep. Its
    sequence/status axis is intentionally distinct from RTBW producer age.
    """

    row: WaterfallLineFrame
    stamp: SweepRowStamp

    def __post_init__(self) -> None:
        if self.row.timestamp_known or self.row.timestamp_ns != 0:
            raise ValueError("Sweep row cannot invent a simultaneous acquisition time")
        if self.row.sequence != self.stamp.sequence:
            raise ValueError("Sweep row and stamp sequence must agree")
        if self.row.values.flags.writeable or self.row.frequency_edges_hz.flags.writeable:
            raise ValueError("Sweep display rows must be immutable")


@dataclass(frozen=True, slots=True)
class WaterfallDisplayConfig:
    """Presentation knobs validated against the fixed shared display budget."""

    history_seconds: int = 10
    rows_per_second: int = 30
    palette: WaterfallPalette = WaterfallPalette.VIRIDIS
    level_min: float = -120.0
    level_max: float = -20.0
    direction: WaterfallDirection = WaterfallDirection.NEWEST_AT_TOP
    follow_spectrum_levels: bool = True

    def __post_init__(self) -> None:
        if self.rows_per_second not in (30, 60, 120):
            raise ValueError("waterfall rows_per_second must be one of 30, 60 or 120")
        maximum_history = DEFAULT_WATERFALL_PRESENTATION_BUDGET.max_history_seconds(self.rows_per_second)
        if not 1 <= self.history_seconds <= maximum_history:
            raise ValueError(
                "waterfall history_seconds exceeds the existing display budget for rows_per_second"
            )
        if not isfinite(self.level_min) or not isfinite(self.level_max) or self.level_min >= self.level_max:
            raise ValueError("waterfall display levels must be finite and ascending")
        object.__setattr__(self, "palette", WaterfallPalette(self.palette))
        object.__setattr__(self, "direction", WaterfallDirection(self.direction))

    def dimensions(self, columns: int) -> tuple[int, int]:
        """Return verified ring dimensions for an explicit incoming line width."""

        return DEFAULT_WATERFALL_PRESENTATION_BUDGET.dimensions(
            self.history_seconds,
            self.rows_per_second,
            int(columns),
        )

    @property
    def interval_ns(self) -> int:
        return int(1_000_000_000 // self.rows_per_second)


def adapt_waterfall_line(frame: object) -> WaterfallLineFrame:
    """Validate only the declared V2 input shape without reading backend owners."""

    if isinstance(frame, WaterfallLineFrame):
        return frame
    return WaterfallLineFrame(
        values=np.asarray(getattr(frame, "values", None)),
        frequency_edges_hz=np.asarray(getattr(frame, "frequency_edges_hz", None)),
        timestamp_ns=cast(int, getattr(frame, "timestamp_ns", None)),
        configuration_generation=cast(int | str, getattr(frame, "configuration_generation", None)),
        unit_label=str(getattr(frame, "unit", getattr(frame, "unit_label", ""))),
    )


def _validate_generation(value: int | str) -> None:
    if isinstance(value, bool):
        raise ValueError("waterfall configuration_generation must be an integer or non-empty string")
    if isinstance(value, int) and value >= 0:
        return
    if isinstance(value, str) and value.strip():
        return
    raise ValueError("waterfall configuration_generation must be an integer or non-empty string")
