"""Bounded presentation adapters for an already coherent Live bundle."""

from __future__ import annotations

import json
import numpy as np

from sdr_monitor.domain.live import LivePersistenceFrame, LiveSpectrumFrame
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.domain.sweep_statistics import SweepStatisticsFrame

from ..spectrum.persistence_contracts import DensityValueMode, PersistenceDensityFrame
from ..waterfall.contracts import WaterfallLineFrame, SweepWaterfallLine
from ..waterfall.sweep_rows import SweepRowStamp, SweepRowState


_MAX_WATERFALL_COLUMNS = 2048


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


def persistence_density_from_sweep(frame: SweepStatisticsFrame) -> PersistenceDensityFrame:
    """Display native pooled-bin probabilities, not a Qt-derived histogram.

    Level edges describe native bins; probability and physical cells are views
    of the producer snapshot. Unknown columns remain NaN/transparent.
    """
    levels = np.linspace(frame.power_min_db, frame.power_max_db, frame.probability.shape[0] + 1)
    levels.setflags(write=False)
    return PersistenceDensityFrame(
        density=frame.probability, frequency_edges_hz=frame.density_frequency_edges_hz,
        level_edges=levels, value_mode=DensityValueMode.PROBABILITY, level_unit=frame.unit,
    )


def waterfall_line_from_spectrum(frame: LiveSpectrumFrame) -> WaterfallLineFrame:
    """Make a bounded, peak-preserving *presentation* row from a spectrum.

    The analytical FFT remains untouched.  Above the existing Waterfall
    ceiling, output cells cover equal physical frequency intervals across the
    complete native span.  Power-of-two FFT sizes therefore use exact integer
    groups (4096 -> 2048, 16384 -> 2048); other widths use the same regular
    physical output grid and assign every source-bin centre to exactly one
    cell.  A cell containing any unknown sample remains NaN, so display LOD
    cannot bridge an acquisition/analysis gap with a neighbouring peak.
    """
    native_edges = _regular_edges(frame.frequencies_hz)
    values = frame.values
    if values.size <= _MAX_WATERFALL_COLUMNS:
        reduced_values, reduced_edges = values, native_edges
    else:
        reduced_values, reduced_edges = _reduce_waterfall_columns(values, native_edges)
    return WaterfallLineFrame(
        values=reduced_values, frequency_edges_hz=reduced_edges,
        timestamp_ns=int(frame.timestamp_ns),
        configuration_generation=int(frame.config_generation), unit_label=frame.unit,
        timestamp_known=_known_waterfall_timestamp(frame), sequence=int(frame.sequence),
    )


def _known_waterfall_timestamp(frame: LiveSpectrumFrame) -> bool:
    """Only the published Unix clock with non-unknown quality supports age labels."""
    quality = getattr(frame.timestamp_quality, "value", frame.timestamp_quality)
    return str(getattr(frame, "clock_domain", "")).casefold() == "unix_ns" and str(quality).casefold() != "unknown"


def waterfall_line_from_sweep(frame: SweepLineFrame | SweepProgressFrame) -> SweepWaterfallLine:
    """Presentation LOD only; keep the original measurement/provenance untouched.

    Reuses the RTBW physical grid and peak/NaN-preserving reduction. No FFT,
    averaging or histogram work is performed here. Sweep time remains unknown
    for the whole row; per-segment acquisition records stay on the domain frame.
    """
    edges = _regular_edges(frame.frequencies_hz)
    values = frame.values_db
    if values.size > _MAX_WATERFALL_COLUMNS:
        values, edges = _reduce_waterfall_columns(values, edges)
    stamp = SweepRowStamp(
        sequence=frame.sequence, revision=frame.revision if isinstance(frame, SweepProgressFrame) else 0,
        state=SweepRowState.PARTIAL if isinstance(frame, SweepProgressFrame) else SweepRowState(frame.state.value),
    )
    return SweepWaterfallLine(
        row=WaterfallLineFrame(
            values=values, frequency_edges_hz=edges, timestamp_ns=0,
            configuration_generation="sweep:" + json.dumps((frame.source_id, frame.epoch)),
            unit_label=frame.unit, timestamp_known=False, sequence=frame.sequence,
        ),
        stamp=stamp,
    )


def _reduce_waterfall_columns(values: np.ndarray, native_edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Reduce onto at most 2048 equal-width physical presentation cells."""
    columns = _MAX_WATERFALL_COLUMNS
    source = np.asarray(values, dtype=np.float32).reshape(-1)
    # Mapping source-bin *centres* to equal physical cells retains the full
    # physical span for non-divisible sizes without a narrower final bucket.
    bucket = (((2 * np.arange(source.size, dtype=np.int64) + 1) * columns) // (2 * source.size))
    starts = np.asarray(np.searchsorted(bucket, np.arange(columns), side="left"), dtype=np.int64).reshape(-1)
    # source.size > columns: monotonic centre assignment covers every output
    # cell, so reduceat has no empty group. Retain NaN for a cell containing
    # *any* non-finite source value, including +/-inf. This performs the same
    # peak-preserving display reduction without 2048 Python loops per frame.
    reduced: np.ndarray = np.maximum.reduceat(source, starts)
    finite = np.logical_and.reduceat(np.isfinite(source), starts)
    reduced[~finite] = np.nan
    span = float(native_edges[-1] - native_edges[0])
    edges = float(native_edges[0]) + np.arange(columns + 1, dtype=np.float64) * (span / columns)
    edges[-1] = native_edges[-1]
    reduced.setflags(write=False)
    edges.setflags(write=False)
    return reduced, edges
