"""P13 linear-power stitching of immutable P12 sweep segment results."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math

import numpy as np

from .contracts import (
    CalibrationStatus,
    QualityFlag,
    SpectrumFrame,
    SpectrumUnit,
    SweepSeamMetric,
    SweepSegmentMetadata,
    SweepSpectrumFrame,
)
from .sweep import SweepExecutionResult, SweepSegmentResult, SweepSegmentStatus


class SweepStitchError(ValueError):
    """Raised when segment results cannot be stitched safely."""


@dataclass(frozen=True, slots=True)
class SweepStitchOptions:
    target_spacing_hz: float | None = None
    grid_tolerance: float = 1.0e-6
    edge_taper_bins: int = 8
    uncertainty_floor_db: float = 0.25
    min_overlap_points: int = 3
    apply_overlap_correction: bool = True
    max_target_bins: int = 2_000_000

    def __post_init__(self) -> None:
        if self.target_spacing_hz is not None and (
            not math.isfinite(self.target_spacing_hz) or self.target_spacing_hz <= 0.0
        ):
            raise SweepStitchError("target_spacing_hz must be finite and positive")
        if not math.isfinite(self.grid_tolerance) or self.grid_tolerance < 0.0:
            raise SweepStitchError("grid_tolerance must be finite and non-negative")
        if isinstance(self.edge_taper_bins, bool) or self.edge_taper_bins < 0:
            raise SweepStitchError("edge_taper_bins must be non-negative")
        if not math.isfinite(self.uncertainty_floor_db) or self.uncertainty_floor_db <= 0.0:
            raise SweepStitchError("uncertainty_floor_db must be finite and positive")
        if isinstance(self.min_overlap_points, bool) or self.min_overlap_points < 2:
            raise SweepStitchError("min_overlap_points must be at least two")
        if not isinstance(self.apply_overlap_correction, bool):
            raise SweepStitchError("apply_overlap_correction must be bool")
        if isinstance(self.max_target_bins, bool) or self.max_target_bins <= 0:
            raise SweepStitchError("max_target_bins must be positive")


@dataclass(slots=True)
class _SegmentData:
    result: SweepSegmentResult
    frequencies_hz: np.ndarray
    power: np.ndarray
    uncertainty_db: np.ndarray
    crop_ranges: tuple[tuple[float, float, int, int], ...]
    quality_flags: QualityFlag
    unit: SpectrumUnit
    calibration_status: CalibrationStatus
    calibration_profile_id: str | None
    nominal_rbw_hz: float
    config_generation: int


_CALIBRATION_RANK = {
    CalibrationStatus.NOT_APPLICABLE: 0,
    CalibrationStatus.APPLIED: 1,
    CalibrationStatus.UNCALIBRATED: 2,
    CalibrationStatus.INTERPOLATED: 3,
    CalibrationStatus.EXTRAPOLATED: 4,
    CalibrationStatus.INVALID: 5,
}


def _power_from_db(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    result = np.zeros(values.size, dtype=np.float64)
    finite = np.isfinite(values)
    result[finite] = np.power(10.0, values[finite] / 10.0)
    result[np.isneginf(values)] = 0.0
    return result


def _db_from_power(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    result = np.full(values.size, np.nan, dtype=np.float64)
    positive = np.isfinite(values) & (values > 0.0)
    result[positive] = 10.0 * np.log10(values[positive])
    return result


def _quantiles(values: np.ndarray) -> tuple[float, float, float]:
    absolute = np.abs(values[np.isfinite(values)])
    if not absolute.size:
        raise SweepStitchError("seam contains no finite power comparisons")
    return (
        float(np.percentile(absolute, 50.0)),
        float(np.percentile(absolute, 95.0)),
        float(np.max(absolute)),
    )


def _status_from_codes(codes: Iterable[CalibrationStatus]) -> CalibrationStatus:
    values = tuple(codes)
    return max(values, key=lambda item: _CALIBRATION_RANK[item]) if values else CalibrationStatus.INVALID


def _frame_profile_id(frames: tuple[SpectrumFrame, ...]) -> str | None:
    profile_ids = {frame.calibration_profile_id for frame in frames if frame.calibration_profile_id}
    if len(profile_ids) > 1:
        raise SweepStitchError("segments use incompatible calibration profiles")
    return next(iter(profile_ids), None)


def _collapse_result(result: SweepSegmentResult) -> _SegmentData:
    if result.status is not SweepSegmentStatus.COMPLETED or not result.frames:
        raise SweepStitchError("cannot collapse a missing segment")
    frames = tuple(result.frames)
    first = frames[0]
    frequency = np.asarray(first.frequencies_hz, dtype=np.float64)
    if frequency.size < 2 or not np.all(np.isfinite(frequency)) or not np.all(np.diff(frequency) > 0.0):
        raise SweepStitchError(f"segment {result.plan.segment_index} has an invalid frequency grid")
    spacing = float(np.median(np.diff(frequency)))
    powers: list[np.ndarray] = []
    uncertainties: list[np.ndarray] = []
    quality_flags = QualityFlag(result.quality_flags)
    generations: set[int] = set()
    for frame in frames:
        if frame.unit is not first.unit:
            raise SweepStitchError("all segments must use one SpectrumUnit")
        if frame.frequencies_hz.size != frequency.size or not np.allclose(
            frame.frequencies_hz,
            frequency,
            rtol=0.0,
            atol=max(1.0e-12, spacing * 1.0e-6),
        ):
            raise SweepStitchError("dwell frames in one segment use incompatible grids")
        if not math.isclose(frame.nominal_rbw_hz, first.nominal_rbw_hz, rel_tol=1.0e-6, abs_tol=1.0e-12):
            raise SweepStitchError("dwell frames in one segment use incompatible RBW")
        powers.append(_power_from_db(np.asarray(frame.values, dtype=np.float64)))
        uncertainties.append(np.full(frequency.size, frame.estimated_uncertainty_db, dtype=np.float64))
        quality_flags |= frame.quality_flags
        generations.add(int(frame.config_generation))
    if len(generations) > 1:
        raise SweepStitchError("one segment contains multiple config generations")
    uncertainty_stack = np.stack(uncertainties, axis=0)
    finite_uncertainty = np.isfinite(uncertainty_stack) & (uncertainty_stack >= 0.0)
    uncertainty = np.full(frequency.size, np.nan, dtype=np.float64)
    if np.any(finite_uncertainty):
        squares = np.where(finite_uncertainty, uncertainty_stack ** 2, np.nan)
        uncertainty = np.sqrt(np.nanmean(squares, axis=0))
    crop_ranges: list[tuple[float, float, int, int]] = []
    for crop in result.plan.crop_ranges:
        start_bin = max(0, min(frequency.size, crop.start_bin))
        stop_bin = max(start_bin, min(frequency.size, crop.stop_bin))
        if stop_bin > start_bin:
            crop_ranges.append((
                crop.start_frequency_hz,
                crop.stop_frequency_hz,
                start_bin,
                stop_bin,
            ))
    if not crop_ranges:
        raise SweepStitchError(f"segment {result.plan.segment_index} contains no crop bins")
    return _SegmentData(
        result=result,
        frequencies_hz=frequency,
        power=np.mean(np.stack(powers, axis=0), axis=0),
        uncertainty_db=uncertainty,
        crop_ranges=tuple(crop_ranges),
        quality_flags=quality_flags,
        unit=first.unit,
        calibration_status=max(
            (frame.calibration_status for frame in frames),
            key=lambda item: _CALIBRATION_RANK[item],
        ),
        calibration_profile_id=_frame_profile_id(frames),
        nominal_rbw_hz=float(first.nominal_rbw_hz),
        config_generation=next(iter(generations), 0),
    )


def _regrid(data: _SegmentData, target: np.ndarray, source: np.ndarray) -> np.ndarray:
    output = np.full(target.size, np.nan, dtype=np.float64)
    for start_hz, stop_hz, start_bin, stop_bin in data.crop_ranges:
        source_frequency = data.frequencies_hz[start_bin:stop_bin]
        source_values = source[start_bin:stop_bin]
        valid = np.isfinite(source_frequency) & np.isfinite(source_values)
        if np.count_nonzero(valid) < 2:
            continue
        source_frequency = source_frequency[valid]
        source_values = source_values[valid]
        mask = (target >= start_hz) & (target <= stop_hz)
        if np.any(mask):
            output[mask] = np.interp(
                target[mask], source_frequency, source_values, left=np.nan, right=np.nan
            )
    return output


def _overlap_difference(
    left: _SegmentData,
    right: _SegmentData,
    target: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    start_hz = max(left.result.plan.requested_start_hz, right.result.plan.requested_start_hz)
    stop_hz = min(left.result.plan.requested_stop_hz, right.result.plan.requested_stop_hz)
    if stop_hz <= start_hz:
        return np.empty(0), np.empty(0)
    points = target[(target >= start_hz) & (target <= stop_hz)]
    if not points.size:
        return np.empty(0), np.empty(0)
    left_db = _db_from_power(_regrid(left, points, left.power))
    right_db = _db_from_power(_regrid(right, points, right.power))
    valid = np.isfinite(left_db) & np.isfinite(right_db)
    return points[valid], right_db[valid] - left_db[valid]


def _edge_weight(
    data: _SegmentData,
    target: np.ndarray,
    options: SweepStitchOptions,
) -> tuple[np.ndarray, np.ndarray]:
    weights = np.zeros(target.size, dtype=np.float64)
    edges = np.zeros(target.size, dtype=bool)
    bin_width = data.result.plan.sample_rate_hz / data.result.plan.fft_size
    width = max(options.edge_taper_bins * bin_width, np.finfo(float).eps)
    for start_hz, stop_hz, _, _ in data.crop_ranges:
        mask = (target >= start_hz) & (target <= stop_hz)
        if not np.any(mask):
            continue
        distance = np.minimum(target[mask] - start_hz, stop_hz - target[mask])
        if options.edge_taper_bins == 0:
            taper = np.ones(distance.size, dtype=np.float64)
        else:
            taper = np.sin(0.5 * np.pi * np.clip(distance / width, 0.0, 1.0)) ** 2
            taper = 0.05 + 0.95 * taper
        weights[mask] = np.maximum(weights[mask], taper)
        edges[mask] |= distance <= width
    return weights, edges


def _segment_metadata(result: SweepSegmentResult) -> SweepSegmentMetadata:
    return SweepSegmentMetadata(
        segment_index=result.plan.segment_index,
        center_frequency_hz=result.plan.center_frequency_hz,
        actual_start_hz=result.plan.actual_start_hz,
        actual_stop_hz=result.plan.actual_stop_hz,
        quality_flags=QualityFlag(result.quality_flags),
    )


def stitch_sweep(
    execution: SweepExecutionResult,
    options: SweepStitchOptions | None = None,
) -> SweepSpectrumFrame:
    """Create one immutable full-span frame from one P12 execution."""

    if not isinstance(execution, SweepExecutionResult):
        raise TypeError("execution must be SweepExecutionResult")
    options = options or SweepStitchOptions()
    plan = execution.plan
    by_index = {item.plan.segment_index: item for item in execution.segments}
    expected = set(range(len(plan.segments)))
    if set(by_index) != expected or len(by_index) != len(execution.segments):
        raise SweepStitchError("execution must contain exactly one result per planned segment")
    ordered_results = tuple(by_index[index] for index in sorted(by_index))
    completed = [
        _collapse_result(item)
        for item in ordered_results
        if item.status is SweepSegmentStatus.COMPLETED and item.frames
    ]
    units = {item.unit for item in completed}
    if len(units) > 1:
        raise SweepStitchError("cannot stitch mixed spectrum units")
    unit = next(iter(units), SpectrumUnit.DBFS_BIN)
    profiles = {item.calibration_profile_id for item in completed if item.calibration_profile_id}
    if len(profiles) > 1:
        raise SweepStitchError("cannot stitch mixed calibration profiles")
    profile_id = next(iter(profiles), None)
    generations = {item.config_generation for item in completed}
    if len(generations) > 1:
        raise SweepStitchError("cannot stitch mixed config generations")
    config_generation = next(iter(generations), 0)

    if completed:
        spacings = np.asarray([np.median(np.diff(item.frequencies_hz)) for item in completed])
        base_spacing = float(np.median(spacings))
        spread = float(np.max(np.abs(spacings - base_spacing)))
        if options.target_spacing_hz is None and spread > options.grid_tolerance * base_spacing:
            raise SweepStitchError("nonuniform grids require explicit target_spacing_hz")
        spacing = float(options.target_spacing_hz or base_spacing)
        nominal_rbw = float(np.median([item.nominal_rbw_hz for item in completed]))
    else:
        spacing = plan.config.sample_rate_hz / plan.config.fft_size
        nominal_rbw = spacing
    span = plan.requested_stop_hz - plan.requested_start_hz
    target_count = int(math.floor(span / spacing)) + 1
    if target_count <= 1:
        raise SweepStitchError("target grid contains fewer than two bins")
    if target_count > options.max_target_bins:
        raise SweepStitchError("target grid exceeds max_target_bins")
    target = plan.requested_start_hz + np.arange(target_count, dtype=np.float64) * spacing

    corrections = {index: 0.0 for index in range(len(plan.segments))}
    seams: list[SweepSeamMetric] = []
    for current in completed:
        prior = [
            item for item in completed
            if item.result.plan.segment_index < current.result.plan.segment_index
            and min(item.result.plan.requested_stop_hz, current.result.plan.requested_stop_hz)
            > max(item.result.plan.requested_start_hz, current.result.plan.requested_start_hz)
        ]
        if not prior:
            continue
        reference = prior[-1]
        _, before_difference = _overlap_difference(reference, current, target)
        if before_difference.size < options.min_overlap_points:
            continue
        correction = float(np.median(before_difference)) if options.apply_overlap_correction else 0.0
        if correction:
            current.power *= 10.0 ** (-correction / 10.0)
        _, after_difference = _overlap_difference(reference, current, target)
        before = _quantiles(before_difference)
        after = _quantiles(after_difference)
        start_hz = max(reference.result.plan.requested_start_hz, current.result.plan.requested_start_hz)
        stop_hz = min(reference.result.plan.requested_stop_hz, current.result.plan.requested_stop_hz)
        seams.append(SweepSeamMetric(
            left_segment_index=reference.result.plan.segment_index,
            right_segment_index=current.result.plan.segment_index,
            overlap_start_hz=start_hz,
            overlap_stop_hz=stop_hz,
            sample_count=int(after_difference.size),
            correction_db=correction,
            before_p50_db=before[0],
            before_p95_db=before[1],
            before_max_db=before[2],
            after_p50_db=after[0],
            after_p95_db=after[1],
            after_max_db=after[2],
        ))
        corrections[current.result.plan.segment_index] = correction

    power_sum = np.zeros(target.size, dtype=np.float64)
    weight_sum = np.zeros(target.size, dtype=np.float64)
    uncertainty_sum = np.zeros(target.size, dtype=np.float64)
    uncertainty_den = np.zeros(target.size, dtype=np.float64)
    best_weight = np.zeros(target.size, dtype=np.float64)
    source_indices = np.full(target.size, -1, dtype=np.int32)
    source_counts = np.zeros(target.size, dtype=np.uint16)
    quality = np.zeros(target.size, dtype=np.uint16)
    invalid_code = np.uint8(list(CalibrationStatus).index(CalibrationStatus.INVALID))
    calibration_codes = np.full(target.size, invalid_code, dtype=np.uint8)
    calibration_ranks = np.full(target.size, -1, dtype=np.int16)

    for data in completed:
        regridded_power = _regrid(data, target, data.power)
        regridded_uncertainty = _regrid(data, target, data.uncertainty_db)
        edge_weight, edge_mask = _edge_weight(data, target, options)
        finite_power = np.isfinite(regridded_power) & (regridded_power >= 0.0)
        usable = finite_power & (edge_weight > 0.0)
        uncertainty_weight = np.ones(target.size, dtype=np.float64)
        finite_uncertainty = np.isfinite(regridded_uncertainty) & (regridded_uncertainty >= 0.0)
        uncertainty_weight[finite_uncertainty] = 1.0 / np.maximum(
            regridded_uncertainty[finite_uncertainty],
            options.uncertainty_floor_db,
        ) ** 2
        weights = edge_weight * uncertainty_weight
        usable &= np.isfinite(weights) & (weights > 0.0)
        power_sum[usable] += regridded_power[usable] * weights[usable]
        weight_sum[usable] += weights[usable]
        known = usable & finite_uncertainty
        uncertainty_sum[known] += regridded_uncertainty[known] * weights[known]
        uncertainty_den[known] += weights[known]
        segment_flags = np.uint16(int(data.quality_flags))
        quality[usable] |= segment_flags
        quality[edge_mask & usable] |= np.uint16(QualityFlag.EDGE_BIN)
        source_counts[usable] += np.uint16(1)
        preferred = usable & (weights > best_weight)
        source_indices[preferred] = np.int32(data.result.plan.segment_index)
        best_weight[preferred] = weights[preferred]
        code = np.uint8(list(CalibrationStatus).index(data.calibration_status))
        rank = _CALIBRATION_RANK[data.calibration_status]
        better = usable & (rank > calibration_ranks)
        calibration_codes[better] = code
        calibration_ranks[usable] = np.maximum(calibration_ranks[usable], rank)
    overlap = source_counts > 1
    quality[overlap] |= np.uint16(QualityFlag.STITCH_OVERLAP)
    missing = np.zeros(target.size, dtype=bool)
    for result in ordered_results:
        if result.status is not SweepSegmentStatus.COMPLETED or not result.frames:
            missing |= (
                (target >= result.plan.requested_start_hz)
                & (target <= result.plan.requested_stop_hz)
            )
    quality[missing] |= np.uint16(QualityFlag.MISSING_SEGMENT)

    values = np.full(target.size, np.nan, dtype=np.float32)
    present = weight_sum > 0.0
    values[present] = (10.0 * np.log10(power_sum[present] / weight_sum[present])).astype(np.float32)
    uncertainty = np.full(target.size, np.nan, dtype=np.float32)
    known_uncertainty = uncertainty_den > 0.0
    uncertainty[known_uncertainty] = (
        uncertainty_sum[known_uncertainty] / uncertainty_den[known_uncertainty]
    ).astype(np.float32)
    missing |= ~present
    quality[missing] |= np.uint16(QualityFlag.MISSING_SEGMENT)

    status_values = tuple(
        list(CalibrationStatus)[int(code)]
        for code in np.unique(calibration_codes[present])
    )
    overall_status = _status_from_codes(status_values)
    segment_metadata = tuple(_segment_metadata(item) for item in ordered_results)
    actual_start = min(item.plan.actual_start_hz for item in ordered_results)
    actual_stop = max(item.plan.actual_stop_hz for item in ordered_results)
    return SweepSpectrumFrame(
        sweep_id=execution.sweep_id,
        started_ns=execution.started_ns,
        completed_ns=execution.completed_ns,
        requested_start_hz=plan.requested_start_hz,
        requested_stop_hz=plan.requested_stop_hz,
        actual_start_hz=actual_start,
        actual_stop_hz=actual_stop,
        nominal_rbw_hz=nominal_rbw,
        frequencies_hz=target,
        values=values,
        quality_flags_per_bin=quality,
        segments=segment_metadata,
        config_generation=config_generation,
        unit=unit,
        calibration_status=overall_status,
        calibration_profile_id=profile_id,
        source_segment_indices_per_bin=source_indices,
        source_segment_count_per_bin=source_counts,
        uncertainty_db_per_bin=uncertainty,
        calibration_status_per_bin=calibration_codes,
        seam_metrics=tuple(seams),
        correction_db_by_segment=tuple(corrections[index] for index in range(len(plan.segments))),
    )


class SweepStitcher:
    """Reusable stateless wrapper around stitch_sweep."""

    def __init__(self, options: SweepStitchOptions | None = None) -> None:
        self.options = options or SweepStitchOptions()

    def stitch(self, execution: SweepExecutionResult) -> SweepSpectrumFrame:
        return stitch_sweep(execution, self.options)


__all__ = [
    "SweepStitchError",
    "SweepStitchOptions",
    "SweepStitcher",
    "stitch_sweep",
]