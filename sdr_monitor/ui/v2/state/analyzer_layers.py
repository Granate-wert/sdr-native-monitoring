"""Bounded presentation adapters for an already coherent Live bundle."""

from __future__ import annotations

import numpy as np

from sdr_monitor.domain.live import LivePersistenceFrame, LiveSpectrumFrame

from ..spectrum.persistence_contracts import DensityValueMode, PersistenceDensityFrame
from ..waterfall.contracts import WaterfallLineFrame


def _regular_edges(centers: np.ndarray) -> np.ndarray:
    values = np.asarray(centers, dtype=np.float64).reshape(-1)
    if values.size < 2 or not np.all(np.isfinite(values)):
        raise ValueError("physical centers require at least two finite points")
    spacing = np.diff(values)
    if np.any(spacing <= 0.0) or not np.allclose(spacing, spacing[0], rtol=1e-9, atol=1e-9):
        raise ValueError("presentation layers require a regular physical grid")
    edges: np.ndarray = np.empty(values.size + 1, dtype=np.float64)
    edges[:-1] = values - spacing[0] / 2.0
    edges[-1] = values[-1] + spacing[0] / 2.0
    edges.setflags(write=False)
    return edges


def persistence_density_from_native(
    frame: object, *, value_mode: DensityValueMode = DensityValueMode.PROBABILITY,
) -> PersistenceDensityFrame | None:
    """Scale a native histogram using its producer-owned declared semantic."""
    if not isinstance(frame, LivePersistenceFrame):
        return None
    value_mode = DensityValueMode(value_mode)
    scale = frame.probability_scale if value_mode is DensityValueMode.PROBABILITY else frame.count_scale
    if not np.isfinite(scale) or not np.isfinite(frame.power_min_db) or not np.isfinite(frame.power_max_db):
        return None
    if frame.power_max_db <= frame.power_min_db:
        return None
    density = np.multiply(frame.density, scale, dtype=np.float32)
    if np.any(np.isfinite(density) & (density < 0.0)):
        return None
    if value_mode is DensityValueMode.PROBABILITY and np.any(np.isfinite(density) & (density > 1.0)):
        return None
    density.setflags(write=False)
    levels = np.linspace(frame.power_min_db, frame.power_max_db, frame.power_bins + 1)
    levels.setflags(write=False)
    return PersistenceDensityFrame(
        density=density, frequency_edges_hz=_regular_edges(frame.frequencies_hz),
        level_edges=levels, value_mode=value_mode,
        level_unit=frame.unit or "",
    )


def waterfall_line_from_spectrum(frame: LiveSpectrumFrame) -> WaterfallLineFrame:
    """Use the same immutable measurement values and producer timestamp once."""
    return WaterfallLineFrame(
        values=frame.values, frequency_edges_hz=_regular_edges(frame.frequencies_hz),
        timestamp_ns=int(frame.timestamp_ns),
        configuration_generation=int(frame.config_generation), unit_label=frame.unit,
    )
