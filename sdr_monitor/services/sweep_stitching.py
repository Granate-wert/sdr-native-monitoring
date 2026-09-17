"""Bounded, Qt-free multi-segment sweep stitching for the standalone product."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, cast

import numpy as np
from numpy.typing import NDArray

from ..domain.sweep import (
    StitchedSweepGrid,
    SweepBinQuality,
    SweepPlan,
    SweepSeamEvidence,
    SweepSegmentSpectrum,
)


class SweepStitchError(ValueError):
    """Raised before a result could silently mix incompatible segment evidence."""


@dataclass(frozen=True, slots=True)
class SweepStitchOptions:
    """Explicit resource and numerical bounds for one final frequency grid."""

    target_spacing_hz: float | None = None
    edge_taper_bins: int = 8
    min_overlap_points: int = 3
    apply_overlap_correction: bool = True
    max_target_bins: int = 2_000_000
    expected_generation_by_segment: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.target_spacing_hz is not None and (
            not math.isfinite(self.target_spacing_hz) or self.target_spacing_hz <= 0.0
        ):
            raise SweepStitchError("target spacing must be finite and positive")
        if self.edge_taper_bins < 0 or self.min_overlap_points < 2 or self.max_target_bins < 2:
            raise SweepStitchError("invalid bounded sweep stitch options")
        expected = tuple(self.expected_generation_by_segment)
        if (
            any(index < 0 or generation < 0 for index, generation in expected)
            or len({index for index, _generation in expected}) != len(expected)
        ):
            raise SweepStitchError("expected segment generations must be unique and non-negative")


def _power_from_db(values: np.ndarray) -> np.ndarray:
    power: NDArray[np.float64] = np.full(values.size, np.nan, dtype=np.float64)
    finite = np.isfinite(values)
    power[finite] = np.power(10.0, values[finite] / 10.0)
    power[np.isneginf(values)] = 0.0
    return power


def _db_from_power(power: np.ndarray) -> np.ndarray:
    values: NDArray[np.float64] = np.full(power.size, np.nan, dtype=np.float64)
    present = np.isfinite(power) & (power > 0.0)
    values[present] = 10.0 * np.log10(power[present])
    return values


def _target_grid(plan: SweepPlan, spectra: tuple[SweepSegmentSpectrum, ...], options: SweepStitchOptions) -> np.ndarray:
    spacings = np.asarray([np.median(np.diff(item.frequencies_hz)) for item in spectra], dtype=np.float64)
    spacing = float(options.target_spacing_hz if options.target_spacing_hz is not None else np.median(spacings))
    if options.target_spacing_hz is None and not np.allclose(spacings, spacing, rtol=1e-6, atol=1e-9):
        raise SweepStitchError("non-uniform segment grids require an explicit target spacing")
    span_hz = plan.configuration.stop_hz - plan.configuration.start_hz
    count = int(math.floor(span_hz / spacing)) + 1
    if count < 2 or count > options.max_target_bins:
        raise SweepStitchError("stitched sweep target grid exceeds its bounded bin limit")
    return plan.configuration.start_hz + np.arange(count, dtype=np.float64) * spacing


def _usable_source(segment, spectrum: SweepSegmentSpectrum) -> tuple[np.ndarray, np.ndarray]:
    usable = (spectrum.frequencies_hz >= segment.usable_start_hz) & (spectrum.frequencies_hz <= segment.usable_stop_hz)
    return spectrum.frequencies_hz[usable], spectrum.values_db[usable]


def _regrid(segment, spectrum: SweepSegmentSpectrum, target: np.ndarray, correction_db: float) -> tuple[slice, np.ndarray]:
    frequency, values = _usable_source(segment, spectrum)
    if frequency.size < 2:
        return slice(0, 0), np.empty(0, dtype=np.float64)
    start = int(np.searchsorted(target, frequency[0], side="left"))
    stop = int(np.searchsorted(target, frequency[-1], side="right"))
    if stop - start < 1:
        return slice(0, 0), np.empty(0, dtype=np.float64)
    values = cast(NDArray[np.float64], np.interp(target[start:stop], frequency, values, left=np.nan, right=np.nan))
    return slice(start, stop), _power_from_db(values - correction_db)


def _power_on_points(segment, spectrum: SweepSegmentSpectrum, points: np.ndarray) -> np.ndarray:
    """Interpolate one usable segment onto exactly the supplied seam points.

    ``_regrid`` returns a slice relative to a full output grid.  A seam passes
    only a local overlap grid, so its left and right slices can legitimately
    start/end at different offsets when one FFT axis lands between target
    bins.  Returning a full-length NaN-padded vector here preserves the common
    point identity and makes that edge condition explicit instead of relying
    on shape coincidence.
    """

    frequency, values = _usable_source(segment, spectrum)
    if frequency.size < 2:
        return np.full(points.size, np.nan, dtype=np.float64)
    interpolated = cast(NDArray[np.float64], np.interp(points, frequency, values, left=np.nan, right=np.nan))
    return _power_from_db(interpolated)


def _seam(
    left_segment,
    left_spectrum: SweepSegmentSpectrum,
    right_segment,
    right_spectrum: SweepSegmentSpectrum,
    target: np.ndarray,
    options: SweepStitchOptions,
) -> SweepSeamEvidence | None:
    start_hz = max(left_segment.usable_start_hz, right_segment.usable_start_hz)
    stop_hz = min(left_segment.usable_stop_hz, right_segment.usable_stop_hz)
    if stop_hz <= start_hz:
        return None
    points = target[(target >= start_hz) & (target <= stop_hz)]
    if points.size < options.min_overlap_points:
        return None
    left_power = _power_on_points(left_segment, left_spectrum, points)
    right_power = _power_on_points(right_segment, right_spectrum, points)
    left_db = _db_from_power(left_power)
    right_db = _db_from_power(right_power)
    valid = np.isfinite(left_db) & np.isfinite(right_db)
    if int(np.count_nonzero(valid)) < options.min_overlap_points:
        return None
    before = right_db[valid] - left_db[valid]
    correction = float(np.median(before)) if options.apply_overlap_correction else 0.0
    after = before - correction
    return SweepSeamEvidence(
        left_segment.index,
        right_segment.index,
        start_hz,
        stop_hz,
        int(before.size),
        correction,
        float(np.percentile(np.abs(before), 95.0)),
        float(np.percentile(np.abs(after), 95.0)),
    )


def _edge_weights(count: int, taper_bins: int) -> tuple[np.ndarray, np.ndarray]:
    weights: NDArray[np.float64] = np.ones(count, dtype=np.float64)
    edge: NDArray[np.bool_] = np.zeros(count, dtype=bool)
    if taper_bins == 0 or count == 0:
        return weights, edge
    width = min(taper_bins, max(1, count // 2))
    taper = 0.05 + 0.95 * np.sin(np.linspace(0.0, math.pi / 2.0, width, endpoint=False)) ** 2
    weights[:width] = taper
    weights[-width:] = taper[::-1]
    edge[:width] = True
    edge[-width:] = True
    return weights, edge


def stitch_sweep_segments(
    plan: SweepPlan,
    spectra: Iterable[SweepSegmentSpectrum],
    options: SweepStitchOptions | None = None,
) -> StitchedSweepGrid:
    """Stitch known segment spectra in linear power with explicit missing bins.

    Each planned segment is expected at most once.  Absent segments retain NaN
    values and ``MISSING_SEGMENT`` flags; no data is invented to close a gap.
    """

    options = options or SweepStitchOptions()
    entries = tuple(spectra)
    if not entries:
        raise SweepStitchError("at least one completed segment spectrum is required")
    by_index = {entry.segment_index: entry for entry in entries}
    if len(by_index) != len(entries):
        raise SweepStitchError("each sweep segment spectrum must occur at most once")
    planned = {segment.index: segment for segment in plan.segments}
    if not set(by_index).issubset(planned):
        raise SweepStitchError("sweep spectrum refers to a segment outside the plan")
    source_ids = {entry.source_id for entry in entries}
    generations = {entry.config_generation for entry in entries}
    units = {entry.unit for entry in entries}
    if len(source_ids) != 1 or len(units) != 1:
        raise SweepStitchError("cannot stitch mixed source or spectrum unit evidence")
    expected_generations = dict(options.expected_generation_by_segment)
    if len(generations) > 1:
        if not expected_generations:
            raise SweepStitchError("mixed segment generations require an explicit expected generation map")
        if any(expected_generations.get(entry.segment_index) != entry.config_generation for entry in entries):
            raise SweepStitchError("segment generation differs from the explicit sweep transaction map")
    elif expected_generations and any(expected_generations.get(entry.segment_index) != entry.config_generation for entry in entries):
        raise SweepStitchError("segment generation differs from the explicit sweep transaction map")

    target = _target_grid(plan, entries, options)
    power_sum: NDArray[np.float64] = np.zeros(target.size, dtype=np.float64)
    weight_sum: NDArray[np.float64] = np.zeros(target.size, dtype=np.float64)
    quality: NDArray[np.uint16] = np.zeros(target.size, dtype=np.uint16)
    source_indices: NDArray[np.int32] = np.full(target.size, -1, dtype=np.int32)
    best_weight: NDArray[np.float64] = np.zeros(target.size, dtype=np.float64)
    source_count: NDArray[np.uint16] = np.zeros(target.size, dtype=np.uint16)
    admitted_segments: set[int] = set()
    seams: list[SweepSeamEvidence] = []
    corrections: dict[int, float] = {}
    ordered = tuple(segment for segment in plan.segments if segment.index in by_index)
    previous_segment = None
    previous_spectrum = None
    for segment in ordered:
        spectrum = by_index[segment.index]
        if previous_segment is not None and previous_spectrum is not None:
            seam = _seam(previous_segment, previous_spectrum, segment, spectrum, target, options)
            if seam is not None:
                seams.append(seam)
                corrections[segment.index] = seam.correction_db
        previous_segment, previous_spectrum = segment, spectrum

    for segment in plan.segments:
        admitted_spectrum = by_index.get(segment.index)
        if admitted_spectrum is None:
            continue
        grid_slice, power = _regrid(segment, admitted_spectrum, target, corrections.get(segment.index, 0.0))
        if power.size == 0:
            continue
        weights, edge = _edge_weights(power.size, options.edge_taper_bins)
        present = np.isfinite(power) & (power >= 0.0)
        indices: NDArray[np.intp] = np.arange(grid_slice.start, grid_slice.stop, dtype=np.intp)
        admitted = indices[present]
        admitted_weights = weights[present]
        if admitted.size:
            admitted_segments.add(segment.index)
        power_sum[admitted] += power[present] * admitted_weights
        weight_sum[admitted] += admitted_weights
        quality[admitted[edge[present]]] |= np.uint16(SweepBinQuality.EDGE_BIN)
        source_count[admitted] += np.uint16(1)
        replace = admitted_weights > best_weight[admitted]
        source_indices[admitted[replace]] = np.int32(segment.index)
        best_weight[admitted[replace]] = admitted_weights[replace]

    present = weight_sum > 0.0
    values: NDArray[np.float32] = np.full(target.size, np.nan, dtype=np.float32)
    values[present] = _db_from_power(power_sum[present] / weight_sum[present]).astype(np.float32)
    quality[source_count > 1] |= np.uint16(SweepBinQuality.STITCH_OVERLAP)
    quality[~present] |= np.uint16(SweepBinQuality.MISSING_SEGMENT)
    return StitchedSweepGrid(
        source_id=next(iter(source_ids)),
        config_generation=next(iter(generations)) if len(generations) == 1 else None,
        frequencies_hz=target,
        values_db=values,
        quality_flags=quality,
        source_segment_indices=source_indices,
        missing_segment_indices=tuple(segment.index for segment in plan.segments if segment.index not in admitted_segments),
        segment_config_generations=tuple(sorted((entry.segment_index, entry.config_generation) for entry in entries)),
        seams=tuple(seams),
        unit=next(iter(units)),
    )


__all__ = ["SweepStitchError", "SweepStitchOptions", "stitch_sweep_segments"]
