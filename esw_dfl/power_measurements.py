from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Sequence

import numpy as np

from .models import (
    AcquisitionMode,
    MeasurementQuality,
    MeasurementWarning,
    TraceMode,
)
from .time_gated_power import PowerSemantics, dbm_to_mw, mw_to_dbm


class RegionRole(StrEnum):
    SIGNAL = "signal"
    NOISE = "noise"
    MAIN = "main"
    ADJACENT = "adjacent"
    EXCLUDE = "exclude"
    MEASURE = "measure"
    HARMONIC = "harmonic"
    SPURIOUS = "spurious"
    CUSTOM = "custom"


class MaskLimitUnit(StrEnum):
    DBM = "dBm"
    DBM_PER_HZ = "dBm/Hz"
    DBC = "dBc"


@dataclass(slots=True)
class SpectrumFrame:
    frequencies_hz: np.ndarray
    values_db: np.ndarray
    unit: str = "dBm"
    timestamp_s: float | None = None
    frame_index: int | None = None
    source_id: str = ""
    source_revision: str = ""
    acquisition_mode: AcquisitionMode = AcquisitionMode.UNKNOWN
    trace_mode: TraceMode = TraceMode.UNKNOWN
    detector: str = ""
    power_semantics: PowerSemantics = PowerSemantics.UNKNOWN
    rbw_hz: float | None = None
    enbw_hz: float | None = None
    provenance: str = ""

    def __post_init__(self) -> None:
        self.frequencies_hz = np.asarray(self.frequencies_hz, dtype=np.float64).reshape(-1)
        self.values_db = np.asarray(self.values_db, dtype=np.float64).reshape(-1)


@dataclass(frozen=True, slots=True)
class IntegratedPowerResult:
    start_hz: float
    stop_hz: float
    power_mw: float | None
    power_dbm: float | None
    requested_bandwidth_hz: float
    covered_bandwidth_hz: float
    selected_bin_count: int
    valid_bin_count: int
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


def _warning(code: str, message: str, **context: float | int | str | bool) -> MeasurementWarning:
    return MeasurementWarning(code, message, context)


def _worst_quality(*qualities: MeasurementQuality) -> MeasurementQuality:
    rank = {
        MeasurementQuality.EXACT: 0,
        MeasurementQuality.APPROXIMATE: 1,
        MeasurementQuality.LIMITED: 2,
        MeasurementQuality.UNSUPPORTED: 3,
        MeasurementQuality.INVALID: 4,
    }
    return max(qualities, key=rank.__getitem__)


def _unique_warnings(warnings: Iterable[MeasurementWarning]) -> tuple[MeasurementWarning, ...]:
    result: list[MeasurementWarning] = []
    seen: set[tuple[str, str, tuple[tuple[str, object], ...]]] = set()
    for warning in warnings:
        key = (warning.code, warning.message, tuple(sorted(warning.context.items())))
        if key not in seen:
            seen.add(key)
            result.append(warning)
    return tuple(result)


def _trace_warnings(frame: SpectrumFrame) -> tuple[MeasurementQuality, list[MeasurementWarning]]:
    quality = MeasurementQuality.EXACT
    warnings: list[MeasurementWarning] = []
    if frame.trace_mode == TraceMode.MAX_HOLD:
        quality = MeasurementQuality.LIMITED
        warnings.append(_warning("max_hold_source", "Max Hold не является средним спектром"))
    if frame.acquisition_mode == AcquisitionMode.SWEPT:
        quality = _worst_quality(quality, MeasurementQuality.LIMITED)
        warnings.append(_warning("swept_acquisition", "Swept Spectrum не является одновременным кадром"))
    if not frame.detector:
        quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
        warnings.append(_warning("detector_unknown", "Детектор источника неизвестен"))
    return quality, warnings


class SpectrumPowerIntegrator:
    """Integrate logarithmic spectrum values using physical bin overlap."""

    def integrate(
        self,
        frame: SpectrumFrame,
        start_hz: float,
        stop_hz: float,
        *,
        include_partial_bins: bool = True,
    ) -> IntegratedPowerResult:
        low, high = sorted((float(start_hz), float(stop_hz)))
        requested = high - low
        if not np.isfinite(low) or not np.isfinite(high) or requested <= 0:
            return self._invalid(low, high, requested, "invalid_band", "Полоса должна иметь конечную положительную ширину")
        count = min(frame.frequencies_hz.size, frame.values_db.size)
        if count == 0:
            return self._invalid(low, high, requested, "empty_frame", "Кадр не содержит спектральных отсчётов")
        if frame.unit.casefold() not in {"dbm", "dbm/hz"}:
            return IntegratedPowerResult(
                low, high, None, None, requested, 0.0, 0, 0,
                MeasurementQuality.UNSUPPORTED,
                (_warning("unsupported_unit", "Единица спектра не поддерживается", unit=frame.unit),),
            )
        frequencies = frame.frequencies_hz[:count]
        values = frame.values_db[:count]
        finite_frequency = np.isfinite(frequencies)
        frequencies, values = frequencies[finite_frequency], values[finite_frequency]
        if frequencies.size == 0 or np.ptp(frequencies) <= 0:
            return self._invalid(low, high, requested, "invalid_frequency_axis", "Частотная ось отсутствует или вырождена")
        order = np.argsort(frequencies, kind="stable")
        frequencies, values = frequencies[order], values[order]
        unique = np.r_[True, np.diff(frequencies) > 0]
        frequencies, values = frequencies[unique], values[unique]
        if frequencies.size < 2:
            return self._invalid(low, high, requested, "invalid_frequency_axis", "Для интегрирования нужны минимум две разные частоты")
        midpoints = (frequencies[:-1] + frequencies[1:]) / 2.0
        edges = np.r_[
            frequencies[0] - (midpoints[0] - frequencies[0]), midpoints,
            frequencies[-1] + (frequencies[-1] - midpoints[-1]),
        ]
        widths = edges[1:] - edges[:-1]
        overlaps = np.maximum(0.0, np.minimum(edges[1:], high) - np.maximum(edges[:-1], low))
        if not include_partial_bins:
            overlaps = np.where((frequencies >= low) & (frequencies <= high), widths, 0.0)
        selected = overlaps > 0.0
        selected_count = int(np.count_nonzero(selected))
        covered = float(np.sum(overlaps[selected]))
        if selected_count == 0:
            return self._invalid(low, high, requested, "band_outside_axis", "Полоса не пересекает частотную сетку")
        valid = selected & np.isfinite(values)
        valid_count = int(np.count_nonzero(valid))
        if valid_count == 0:
            return self._invalid(low, high, requested, "no_finite_values", "В полосе нет конечных значений")
        quality, warnings = _trace_warnings(frame)
        if valid_count != selected_count:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning(
                "non_finite_values", "NaN/Inf исключены из интегрирования",
                omitted=selected_count - valid_count,
            ))
        semantics = frame.power_semantics
        if frame.unit.casefold() == "dbm/hz":
            if semantics not in (PowerSemantics.PSD_PER_HZ, PowerSemantics.UNKNOWN):
                warnings.append(_warning("unit_semantics_conflict", "Единица dBm/Hz имеет приоритет над выбранной семантикой"))
            semantics = PowerSemantics.PSD_PER_HZ
        linear = dbm_to_mw(values)
        if semantics == PowerSemantics.PSD_PER_HZ:
            weights = overlaps
        elif semantics == PowerSemantics.RBW_FILTERED_POWER:
            effective_bandwidth = frame.enbw_hz or frame.rbw_hz
            if effective_bandwidth is None or effective_bandwidth <= 0:
                return IntegratedPowerResult(
                    low, high, None, None, requested, covered, selected_count, valid_count,
                    MeasurementQuality.INVALID,
                    tuple(warnings) + (_warning("enbw_required", "Для RBW-filtered power требуется положительная ENBW или RBW"),),
                )
            weights = overlaps / effective_bandwidth
        elif semantics == PowerSemantics.POWER_PER_BIN:
            weights = overlaps / widths
        elif semantics == PowerSemantics.UNKNOWN:
            weights = overlaps / widths
            quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
            warnings.append(_warning("power_semantics_unknown", "Семантика мощности отсчёта неизвестна; использована доля ширины бина"))
        else:
            return IntegratedPowerResult(
                low, high, None, None, requested, covered, selected_count, valid_count,
                MeasurementQuality.UNSUPPORTED,
                tuple(warnings) + (_warning("non_spectral_power", "Источник нельзя повторно интегрировать как спектр"),),
            )
        total_mw = float(np.sum(linear[valid] * weights[valid]))
        if not np.isfinite(total_mw) or total_mw <= 0:
            return self._invalid(low, high, requested, "invalid_linear_power", "Интегральная линейная мощность не определена")
        if covered + np.finfo(float).eps < requested:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("partial_frequency_coverage", "Запрошенная полоса покрыта частично", covered_hz=covered, requested_hz=requested))
        return IntegratedPowerResult(
            low, high, total_mw, float(mw_to_dbm(total_mw)), requested, covered,
            selected_count, valid_count, quality, tuple(warnings),
        )

    @staticmethod
    def _invalid(low: float, high: float, bandwidth: float, code: str, message: str) -> IntegratedPowerResult:
        return IntegratedPowerResult(
            low, high, None, None, bandwidth, 0.0, 0, 0,
            MeasurementQuality.INVALID, (_warning(code, message),),
        )


@dataclass(frozen=True, slots=True)
class SingleChannelPowerResult:
    source_id: str
    integrated: IntegratedPowerResult
    mean_density_dbm_hz: float | None
    peak_dbm: float | None
    peak_frequency_hz: float | None


class SingleChannelPowerService:
    def __init__(self, integrator: SpectrumPowerIntegrator | None = None) -> None:
        self.integrator = integrator or SpectrumPowerIntegrator()

    def measure(self, frame: SpectrumFrame, start_hz: float, stop_hz: float) -> SingleChannelPowerResult:
        integrated = self.integrator.integrate(frame, start_hz, stop_hz)
        density = None
        if integrated.power_dbm is not None and integrated.requested_bandwidth_hz > 0:
            density = integrated.power_dbm - 10.0 * np.log10(integrated.requested_bandwidth_hz)
        low, high = sorted((start_hz, stop_hz))
        finite = (
            (frame.frequencies_hz >= low) & (frame.frequencies_hz <= high)
            & np.isfinite(frame.values_db)
        )
        peak_dbm: float | None
        peak_hz: float | None
        if np.any(finite):
            candidates = np.flatnonzero(finite)
            index = int(candidates[np.argmax(frame.values_db[candidates])])
            peak_dbm, peak_hz = float(frame.values_db[index]), float(frame.frequencies_hz[index])
        else:
            peak_dbm = None
            peak_hz = None
        return SingleChannelPowerResult(frame.source_id, integrated, density, peak_dbm, peak_hz)


class TemporalMode(StrEnum):
    CURRENT = "current"
    MEAN = "mean"
    MAXIMUM = "maximum"
    ACTIVE_MEAN = "active_mean"
    INACTIVE_MEAN = "inactive_mean"


@dataclass(frozen=True, slots=True)
class AclrBandResult:
    name: str
    center_hz: float
    bandwidth_hz: float
    power_dbm: float | None
    aclr_db: float | None
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class AclrResult:
    main: AclrBandResult
    adjacent: tuple[AclrBandResult, ...]
    temporal_mode: TemporalMode
    frame_statistics: tuple[AclrFrameStatistics, ...] = ()


@dataclass(frozen=True, slots=True)
class AclrFrameStatistics:
    name: str
    minimum_db: float | None
    median_db: float | None
    maximum_db: float | None
    percentile_95_db: float | None


@dataclass(frozen=True, slots=True)
class AdjacentPairDefinition:
    name: str
    offset_hz: float
    bandwidth_hz: float
    enabled: bool = True


class AclrService:
    def __init__(self, integrator: SpectrumPowerIntegrator | None = None) -> None:
        self.integrator = integrator or SpectrumPowerIntegrator()

    @staticmethod
    def _select_frames(
        frames: Sequence[SpectrumFrame], mode: TemporalMode, activity_mask: np.ndarray | None
    ) -> list[SpectrumFrame]:
        if not frames:
            return []
        if mode == TemporalMode.CURRENT:
            return [frames[-1]]
        if mode == TemporalMode.MAXIMUM:
            return list(frames)
        if mode in (TemporalMode.ACTIVE_MEAN, TemporalMode.INACTIVE_MEAN):
            if activity_mask is None or activity_mask.size != len(frames):
                return []
            desired = activity_mask if mode == TemporalMode.ACTIVE_MEAN else ~activity_mask
            return [frame for frame, selected in zip(frames, desired, strict=True) if selected]
        return list(frames)

    def measure(
        self,
        frames: Sequence[SpectrumFrame],
        center_hz: float,
        main_bandwidth_hz: float,
        offset_hz: float,
        *,
        adjacent_bandwidth_hz: float | None = None,
        adjacent_pairs: int = 1,
        temporal_mode: TemporalMode = TemporalMode.CURRENT,
        activity_mask: np.ndarray | None = None,
        pair_definitions: Sequence[AdjacentPairDefinition] | None = None,
    ) -> AclrResult:
        if main_bandwidth_hz <= 0 or offset_hz <= 0 or adjacent_pairs < 1:
            raise ValueError("Полоса, смещение и число соседних пар должны быть положительными")
        selected = self._select_frames(frames, temporal_mode, activity_mask)
        adjacent_bandwidth_hz = adjacent_bandwidth_hz or main_bandwidth_hz
        bands = [("Main", center_hz, main_bandwidth_hz)]
        if pair_definitions is None:
            pair_definitions = tuple(
                AdjacentPairDefinition(str(order), offset_hz * order, adjacent_bandwidth_hz)
                for order in range(1, adjacent_pairs + 1)
            )
        for order, pair in enumerate((pair for pair in pair_definitions if pair.enabled), 1):
            bands.extend((
                (f"Lower {pair.name or order}", center_hz - pair.offset_hz, pair.bandwidth_hz),
                (f"Upper {pair.name or order}", center_hz + pair.offset_hz, pair.bandwidth_hz),
            ))
        measured: list[AclrBandResult] = []
        per_frame_power: list[np.ndarray] = []
        for name, band_center, bandwidth in bands:
            results = [self.integrator.integrate(frame, band_center - bandwidth / 2, band_center + bandwidth / 2) for frame in selected]
            valid = np.asarray([item.power_mw for item in results if item.power_mw is not None], dtype=np.float64)
            qualities = [item.quality for item in results] or [MeasurementQuality.INVALID]
            warnings = _unique_warnings(warning for item in results for warning in item.warnings)
            if valid.size:
                power_mw = float(np.max(valid) if temporal_mode == TemporalMode.MAXIMUM else np.mean(valid))
                power_dbm = float(mw_to_dbm(power_mw))
            else:
                power_dbm = None
            measured.append(AclrBandResult(name, band_center, bandwidth, power_dbm, None, _worst_quality(*qualities), warnings))
            per_frame_power.append(np.asarray([
                item.power_dbm if item.power_dbm is not None else np.nan for item in results
            ], dtype=np.float64))
        main = measured[0]
        adjacent: list[AclrBandResult] = []
        for item in measured[1:]:
            aclr = main.power_dbm - item.power_dbm if main.power_dbm is not None and item.power_dbm is not None else None
            adjacent.append(AclrBandResult(item.name, item.center_hz, item.bandwidth_hz, item.power_dbm, aclr, item.quality, item.warnings))
        statistics: list[AclrFrameStatistics] = []
        for item, adjacent_frame_power in zip(adjacent, per_frame_power[1:], strict=True):
            values = per_frame_power[0] - adjacent_frame_power
            values = values[np.isfinite(values)]
            statistics.append(AclrFrameStatistics(
                item.name,
                float(np.min(values)) if values.size else None,
                float(np.median(values)) if values.size else None,
                float(np.max(values)) if values.size else None,
                float(np.percentile(values, 95)) if values.size else None,
            ))
        return AclrResult(main, tuple(adjacent), temporal_mode, tuple(statistics))


@dataclass(frozen=True, slots=True)
class MultiChannelDefinition:
    name: str
    center_hz: float
    bandwidth_hz: float
    is_reference: bool = False


class ReferenceMode(StrEnum):
    NEAREST_TX = "nearest_tx"
    SELECTED_TX = "selected_tx"
    TOTAL_TX = "total_tx"
    MAXIMUM_TX = "maximum_tx"
    EACH_CHANNEL = "nearest_tx"
    STRONGEST = "maximum_tx"
    SUM_REFERENCES = "total_tx"


@dataclass(frozen=True, slots=True)
class MultiChannelAclrResult:
    channels: tuple[AclrBandResult, ...]
    reference_power_dbm: float | None
    reference_mode: ReferenceMode


def multi_channel_aclr(
    frame: SpectrumFrame,
    channels: Sequence[MultiChannelDefinition],
    reference_mode: ReferenceMode = ReferenceMode.STRONGEST,
    selected_reference_name: str | None = None,
    integrator: SpectrumPowerIntegrator | None = None,
) -> MultiChannelAclrResult:
    engine = integrator or SpectrumPowerIntegrator()
    raw = [engine.integrate(frame, item.center_hz - item.bandwidth_hz / 2, item.center_hz + item.bandwidth_hz / 2) for item in channels]
    reference_values = [result.power_mw for item, result in zip(channels, raw, strict=True) if item.is_reference and result.power_mw is not None]
    reference_pairs = [
        (item, result.power_mw)
        for item, result in zip(channels, raw, strict=True)
        if item.is_reference and result.power_mw is not None
    ]
    if not reference_values:
        reference_values = [result.power_mw for result in raw if result.power_mw is not None]
    if not reference_values:
        reference_dbm = None
    elif reference_mode == ReferenceMode.TOTAL_TX:
        reference_dbm = float(mw_to_dbm(np.sum(reference_values)))
    elif reference_mode == ReferenceMode.SELECTED_TX:
        selected_power = next(
            (power for item, power in reference_pairs if item.name == selected_reference_name),
            None,
        )
        reference_dbm = float(mw_to_dbm(selected_power)) if selected_power is not None else None
    else:
        reference_dbm = float(mw_to_dbm(np.max(reference_values)))
    output: list[AclrBandResult] = []
    for item, result in zip(channels, raw, strict=True):
        local_reference = reference_dbm
        if reference_mode == ReferenceMode.NEAREST_TX and reference_pairs:
            nearest, nearest_power = min(
                reference_pairs, key=lambda pair: abs(pair[0].center_hz - item.center_hz)
            )
            del nearest
            local_reference = float(mw_to_dbm(nearest_power))
        output.append(AclrBandResult(
            item.name, item.center_hz, item.bandwidth_hz, result.power_dbm,
            local_reference - result.power_dbm
            if local_reference is not None and result.power_dbm is not None and not item.is_reference
            else None,
            result.quality, result.warnings,
        ))
    results = tuple(output)
    return MultiChannelAclrResult(results, reference_dbm, reference_mode)


@dataclass(frozen=True, slots=True)
class OccupiedBandwidthMeasurement:
    lower_hz: float | None
    upper_hz: float | None
    bandwidth_hz: float | None
    total_power_dbm: float | None
    fraction: float
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class WaterfallObwStatistics:
    frame_indices: np.ndarray
    timestamps_s: np.ndarray
    bandwidths_hz: np.ndarray
    current_hz: float | None
    active_mean_hz: float | None
    minimum_hz: float | None
    maximum_hz: float | None


def occupied_bandwidth(
    frame: SpectrumFrame,
    fraction: float = 0.99,
    search_region: tuple[float, float] | None = None,
) -> OccupiedBandwidthMeasurement:
    if not 0 < fraction <= 1:
        raise ValueError("Occupied bandwidth fraction должна быть в диапазоне (0, 1]")
    count = min(frame.frequencies_hz.size, frame.values_db.size)
    finite = np.isfinite(frame.frequencies_hz[:count]) & np.isfinite(frame.values_db[:count])
    x, y = frame.frequencies_hz[:count][finite], frame.values_db[:count][finite]
    if search_region is not None:
        low, high = sorted(search_region)
        selected = (x >= low) & (x <= high)
        x, y = x[selected], y[selected]
    if x.size < 2 or np.ptp(x) <= 0:
        return OccupiedBandwidthMeasurement(None, None, None, None, fraction, MeasurementQuality.INVALID, (_warning("invalid_frequency_axis", "Недостаточно данных для OBW"),))
    order = np.argsort(x)
    x, y = x[order], y[order]
    midpoints = (x[:-1] + x[1:]) / 2
    edges = np.r_[x[0] - (midpoints[0] - x[0]), midpoints, x[-1] + (x[-1] - midpoints[-1])]
    widths = edges[1:] - edges[:-1]
    power = dbm_to_mw(y)
    if frame.power_semantics == PowerSemantics.PSD_PER_HZ or frame.unit.casefold() == "dbm/hz":
        power = power * widths
    elif frame.power_semantics == PowerSemantics.RBW_FILTERED_POWER:
        effective_bandwidth = frame.enbw_hz or frame.rbw_hz
        if not effective_bandwidth or effective_bandwidth <= 0:
            return OccupiedBandwidthMeasurement(None, None, None, None, fraction, MeasurementQuality.INVALID, (_warning("enbw_required", "Для RBW-filtered OBW требуется ENBW/RBW"),))
        power = power * widths / effective_bandwidth
    cumulative = np.cumsum(power)
    total = float(cumulative[-1])
    tail = total * (1.0 - fraction) / 2.0
    low_index = min(int(np.searchsorted(cumulative, tail)), x.size - 1)
    high_index = min(int(np.searchsorted(cumulative, total - tail)), x.size - 1)
    low_before = float(cumulative[low_index - 1]) if low_index else 0.0
    high_before = float(cumulative[high_index - 1]) if high_index else 0.0
    low_fraction = float(np.clip((tail - low_before) / power[low_index], 0.0, 1.0))
    high_fraction = float(np.clip((total - tail - high_before) / power[high_index], 0.0, 1.0))
    lower_hz = float(edges[low_index] + low_fraction * widths[low_index])
    upper_hz = float(edges[high_index] + high_fraction * widths[high_index])
    quality, warnings = _trace_warnings(frame)
    if frame.power_semantics == PowerSemantics.UNKNOWN:
        quality = _worst_quality(quality, MeasurementQuality.APPROXIMATE)
        warnings.append(_warning("power_semantics_unknown", "OBW использует мощность на бин при неизвестной семантике"))
    return OccupiedBandwidthMeasurement(lower_hz, upper_hz, upper_hz - lower_hz, float(mw_to_dbm(total)), fraction, quality, tuple(warnings))


def waterfall_obw_statistics(
    frames: Sequence[SpectrumFrame],
    fraction: float = 0.99,
    search_region: tuple[float, float] | None = None,
    activity_mask: np.ndarray | None = None,
) -> WaterfallObwStatistics:
    results = [occupied_bandwidth(frame, fraction, search_region) for frame in frames]
    bandwidths = np.asarray([
        result.bandwidth_hz if result.bandwidth_hz is not None else np.nan for result in results
    ])
    indices = np.asarray([
        frame.frame_index if frame.frame_index is not None else index
        for index, frame in enumerate(frames)
    ], dtype=np.int64)
    timestamps = np.asarray([
        frame.timestamp_s if frame.timestamp_s is not None else np.nan for frame in frames
    ], dtype=np.float64)
    finite = np.isfinite(bandwidths)
    active = finite if activity_mask is None or activity_mask.size != len(frames) else finite & activity_mask
    return WaterfallObwStatistics(
        indices, timestamps, bandwidths,
        float(bandwidths[-1]) if bandwidths.size and np.isfinite(bandwidths[-1]) else None,
        float(np.mean(bandwidths[active])) if np.any(active) else None,
        float(np.min(bandwidths[finite])) if np.any(finite) else None,
        float(np.max(bandwidths[finite])) if np.any(finite) else None,
    )


@dataclass(frozen=True, slots=True)
class XDbBandwidthMeasurement:
    drop_db: float
    peak_frequency_hz: float | None
    peak_db: float | None
    left_crossing_hz: float | None
    right_crossing_hz: float | None
    bandwidth_hz: float | None
    asymmetry_hz: float | None
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


def x_db_bandwidth(
    frame: SpectrumFrame,
    drop_db: float = 3.0,
    search_region: tuple[float, float] | None = None,
) -> XDbBandwidthMeasurement:
    if drop_db <= 0:
        raise ValueError("X dB должен быть положительным")
    count = min(frame.frequencies_hz.size, frame.values_db.size)
    finite = np.isfinite(frame.frequencies_hz[:count]) & np.isfinite(frame.values_db[:count])
    x, y = frame.frequencies_hz[:count][finite], frame.values_db[:count][finite]
    if search_region is not None:
        low, high = sorted(search_region)
        selected = (x >= low) & (x <= high)
        x, y = x[selected], y[selected]
    if x.size < 3:
        return XDbBandwidthMeasurement(drop_db, None, None, None, None, None, None, MeasurementQuality.INVALID, (_warning("insufficient_points", "Недостаточно точек для X dB bandwidth"),))
    order = np.argsort(x)
    x, y = x[order], y[order]
    peak = int(np.argmax(y))
    threshold = y[peak] - drop_db
    left_candidates = np.flatnonzero(y[:peak] < threshold)
    right_candidates = np.flatnonzero(y[peak + 1:] < threshold)
    quality, warnings = _trace_warnings(frame)
    if not left_candidates.size or not right_candidates.size:
        return XDbBandwidthMeasurement(drop_db, float(x[peak]), float(y[peak]), None, None, None, None, MeasurementQuality.LIMITED, tuple(warnings) + (_warning("crossing_not_found", "Одно или оба пересечения X dB находятся вне области"),))

    def crossing(first: int, second: int) -> float:
        if y[first] == y[second]:
            return float((x[first] + x[second]) / 2)
        return float(x[first] + (threshold - y[first]) * (x[second] - x[first]) / (y[second] - y[first]))

    left_index = int(left_candidates[-1])
    right_index = int(peak + 1 + right_candidates[0])
    left = crossing(left_index, left_index + 1)
    right = crossing(right_index - 1, right_index)
    peak_hz = float(x[peak])
    return XDbBandwidthMeasurement(
        drop_db, peak_hz, float(y[peak]), left, right, right - left,
        (right - peak_hz) - (peak_hz - left), quality, tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class CarrierNoiseResult:
    carrier_power_dbm: float | None
    noise_density_dbm_hz: float | None
    noise_in_signal_band_dbm: float | None
    cn_db: float | None
    cn0_db_hz: float | None
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


def carrier_to_noise(
    frame: SpectrumFrame,
    signal_band: tuple[float, float],
    noise_band: tuple[float, float],
    integrator: SpectrumPowerIntegrator | None = None,
) -> CarrierNoiseResult:
    engine = integrator or SpectrumPowerIntegrator()
    signal = engine.integrate(frame, *signal_band)
    noise = engine.integrate(frame, *noise_band)
    signal_width = abs(signal_band[1] - signal_band[0])
    noise_width = abs(noise_band[1] - noise_band[0])
    warnings = signal.warnings + noise.warnings
    quality = _worst_quality(signal.quality, noise.quality)
    if signal.power_mw is None or noise.power_mw is None or signal_width <= 0 or noise_width <= 0:
        return CarrierNoiseResult(None, None, None, None, None, MeasurementQuality.INVALID, warnings)
    density_mw_hz = noise.power_mw / noise_width
    scaled_noise_mw = density_mw_hz * signal_width
    useful_mw = signal.power_mw - scaled_noise_mw
    if useful_mw <= 0:
        return CarrierNoiseResult(None, float(mw_to_dbm(density_mw_hz)), float(mw_to_dbm(scaled_noise_mw)), None, None, MeasurementQuality.LIMITED, warnings + (_warning("carrier_below_noise", "Мощность сигнала не превышает оценку шума"),))
    carrier_dbm = float(mw_to_dbm(useful_mw))
    noise_dbm = float(mw_to_dbm(scaled_noise_mw))
    density_dbm_hz = float(mw_to_dbm(density_mw_hz))
    cn0 = (
        carrier_dbm - density_dbm_hz
        if frame.power_semantics == PowerSemantics.PSD_PER_HZ or frame.unit.casefold() == "dbm/hz"
        else None
    )
    return CarrierNoiseResult(
        carrier_dbm, density_dbm_hz, noise_dbm, carrier_dbm - noise_dbm,
        cn0, quality, warnings,
    )


@dataclass(frozen=True, slots=True)
class HarmonicPower:
    order: int
    expected_frequency_hz: float
    measured_frequency_hz: float | None
    power_dbm: float | None
    relative_dbc: float | None
    in_range: bool


def harmonic_powers(
    frame: SpectrumFrame,
    fundamental_hz: float,
    count: int = 5,
    measurement_bandwidth_hz: float = 0.0,
    integrator: SpectrumPowerIntegrator | None = None,
) -> tuple[HarmonicPower, ...]:
    engine = integrator or SpectrumPowerIntegrator()
    x = frame.frequencies_hz
    if x.size < 2:
        return ()
    width = measurement_bandwidth_hz or float(np.median(np.abs(np.diff(np.sort(x)))))
    measured: list[tuple[int, float, float | None, float | None, bool]] = []
    for order in range(1, count + 1):
        expected = fundamental_hz * order
        if expected < np.nanmin(x) or expected > np.nanmax(x):
            measured.append((order, expected, None, None, False))
            continue
        result = engine.integrate(frame, expected - width / 2, expected + width / 2)
        measured.append((order, expected, expected, result.power_dbm, result.power_dbm is not None))
    fundamental = next((power for order, _, _, power, valid in measured if order == 1 and valid), None)
    return tuple(HarmonicPower(order, expected, actual, power, power - fundamental if power is not None and fundamental is not None else None, valid) for order, expected, actual, power, valid in measured)


@dataclass(frozen=True, slots=True)
class MeasurementRegion:
    name: str
    start_hz: float
    stop_hz: float
    role: RegionRole = RegionRole.MEASURE
    enabled: bool = True
    color: str = "#3ddc97"
    reference_region: str | None = None

    @property
    def center_hz(self) -> float:
        return (self.start_hz + self.stop_hz) / 2

    @property
    def bandwidth_hz(self) -> float:
        return abs(self.stop_hz - self.start_hz)


@dataclass(frozen=True, slots=True)
class RegionPowerResult:
    region: MeasurementRegion
    integrated: IntegratedPowerResult
    mean_density_dbm_hz: float | None
    peak_db: float | None
    peak_frequency_hz: float | None
    relative_db: float | None = None


def measure_regions(frame: SpectrumFrame, regions: Sequence[MeasurementRegion], integrator: SpectrumPowerIntegrator | None = None) -> tuple[RegionPowerResult, ...]:
    engine = integrator or SpectrumPowerIntegrator()
    measured: list[RegionPowerResult] = []
    for region in regions:
        if not region.enabled or region.role == RegionRole.EXCLUDE:
            continue
        integrated = engine.integrate(frame, region.start_hz, region.stop_hz)
        density = (
            integrated.power_dbm - 10 * np.log10(integrated.requested_bandwidth_hz)
            if integrated.power_dbm is not None and integrated.requested_bandwidth_hz > 0 else None
        )
        low, high = sorted((region.start_hz, region.stop_hz))
        selected = np.flatnonzero(
            (frame.frequencies_hz >= low) & (frame.frequencies_hz <= high)
            & np.isfinite(frame.values_db)
        )
        peak_db: float | None
        peak_hz: float | None
        if selected.size:
            peak_index = int(selected[np.argmax(frame.values_db[selected])])
            peak_db = float(frame.values_db[peak_index])
            peak_hz = float(frame.frequencies_hz[peak_index])
        else:
            peak_db = None
            peak_hz = None
        measured.append(RegionPowerResult(region, integrated, density, peak_db, peak_hz))
    powers = {item.region.name: item.integrated.power_dbm for item in measured}
    output: list[RegionPowerResult] = []
    for item in measured:
        reference = powers.get(item.region.reference_region or "")
        relative = (
            item.integrated.power_dbm - reference
            if item.integrated.power_dbm is not None and reference is not None else None
        )
        output.append(RegionPowerResult(
            item.region, item.integrated, item.mean_density_dbm_hz,
            item.peak_db, item.peak_frequency_hz, relative,
        ))
    return tuple(output)


@dataclass(frozen=True, slots=True)
class SemMaskSegment:
    start_hz: float
    stop_hz: float
    start_limit: float
    stop_limit: float
    unit: MaskLimitUnit = MaskLimitUnit.DBM


@dataclass(frozen=True, slots=True)
class SemViolation:
    frequency_hz: float
    measured_dbm: float
    limit_dbm: float
    excess_db: float


@dataclass(frozen=True, slots=True)
class SemResult:
    passed: bool
    violations: tuple[SemViolation, ...]
    maximum_excess_db: float
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


def spectrum_emission_mask(
    frame: SpectrumFrame,
    segments: Sequence[SemMaskSegment],
    *,
    reference_dbm: float | None = None,
) -> SemResult:
    x, y = frame.frequencies_hz, frame.values_db
    violations: list[SemViolation] = []
    quality, warnings = _trace_warnings(frame)
    for segment in segments:
        selected = np.flatnonzero((x >= min(segment.start_hz, segment.stop_hz)) & (x <= max(segment.start_hz, segment.stop_hz)) & np.isfinite(y))
        if not selected.size:
            quality = _worst_quality(quality, MeasurementQuality.LIMITED)
            warnings.append(_warning("mask_segment_uncovered", "Сегмент маски не покрыт данными", start_hz=segment.start_hz, stop_hz=segment.stop_hz))
            continue
        limits = np.interp(x[selected], [segment.start_hz, segment.stop_hz], [segment.start_limit, segment.stop_limit])
        if segment.unit == MaskLimitUnit.DBM_PER_HZ:
            if not frame.rbw_hz or frame.rbw_hz <= 0:
                quality = MeasurementQuality.INVALID
                warnings.append(_warning("rbw_required_for_mask", "Для маски dBm/Hz требуется RBW"))
                continue
            limits = limits + 10.0 * np.log10(frame.rbw_hz)
        elif segment.unit == MaskLimitUnit.DBC:
            if reference_dbm is None:
                quality = MeasurementQuality.INVALID
                warnings.append(_warning("reference_required_for_dbc", "Для маски dBc требуется опорная мощность"))
                continue
            limits = limits + reference_dbm
        excess = y[selected] - limits
        for local in np.flatnonzero(excess > 0):
            index = int(selected[local])
            violations.append(SemViolation(float(x[index]), float(y[index]), float(limits[local]), float(excess[local])))
    maximum = max((item.excess_db for item in violations), default=0.0)
    return SemResult(not violations and quality != MeasurementQuality.INVALID, tuple(violations), maximum, quality, tuple(warnings))


@dataclass(frozen=True, slots=True)
class SpuriousPeak:
    frequency_hz: float
    level_dbm: float
    prominence_db: float
    integrated_power_dbm: float | None = None
    relative_to_main_db: float | None = None
    distance_from_main_hz: float | None = None
    status: str = "DETECTED"


def spurious_search(
    frame: SpectrumFrame,
    start_hz: float,
    stop_hz: float,
    *,
    minimum_level_dbm: float = -np.inf,
    minimum_prominence_db: float = 0.0,
    minimum_distance_hz: float = 0.0,
    exclusions: Sequence[tuple[float, float]] = (),
    limit: int = 100,
    measurement_bandwidth_hz: float = 0.0,
    main_power_dbm: float | None = None,
    main_center_hz: float | None = None,
    limit_line_dbm: float | None = None,
) -> tuple[SpuriousPeak, ...]:
    x, y = frame.frequencies_hz, frame.values_db
    selected = (x >= min(start_hz, stop_hz)) & (x <= max(start_hz, stop_hz)) & np.isfinite(y)
    for low, high in exclusions:
        selected &= ~((x >= min(low, high)) & (x <= max(low, high)))
    candidates: list[SpuriousPeak] = []
    indices = np.flatnonzero(selected)
    for index in indices:
        if index == 0 or index == y.size - 1 or not selected[index - 1] or not selected[index + 1]:
            continue
        if y[index] < minimum_level_dbm or y[index] < y[index - 1] or y[index] < y[index + 1]:
            continue
        prominence = float(y[index] - max(y[index - 1], y[index + 1]))
        if prominence >= minimum_prominence_db:
            frequency = float(x[index])
            integrated = None
            if measurement_bandwidth_hz > 0:
                integrated = SpectrumPowerIntegrator().integrate(
                    frame, frequency - measurement_bandwidth_hz / 2,
                    frequency + measurement_bandwidth_hz / 2,
                ).power_dbm
            relative = (
                integrated - main_power_dbm
                if integrated is not None and main_power_dbm is not None else None
            )
            candidates.append(SpuriousPeak(
                frequency, float(y[index]), prominence, integrated, relative,
                frequency - main_center_hz if main_center_hz is not None else None,
                "FAIL" if limit_line_dbm is not None and y[index] > limit_line_dbm else "DETECTED",
            ))
    candidates.sort(key=lambda item: item.level_dbm, reverse=True)
    accepted: list[SpuriousPeak] = []
    for candidate in candidates:
        if all(abs(candidate.frequency_hz - item.frequency_hz) >= minimum_distance_hz for item in accepted):
            accepted.append(candidate)
        if len(accepted) >= max(1, limit):
            break
    return tuple(accepted)


@dataclass(frozen=True, slots=True)
class WaterfallPowerStatistics:
    frame_count: int
    valid_frame_count: int
    mean_power_dbm: float | None
    median_power_dbm: float | None
    minimum_power_dbm: float | None
    maximum_power_dbm: float | None
    standard_deviation_db: float | None
    quality: MeasurementQuality
    warnings: tuple[MeasurementWarning, ...] = ()


def waterfall_power_statistics(
    frames: Iterable[SpectrumFrame],
    start_hz: float,
    stop_hz: float,
    *,
    cancel: threading.Event | None = None,
    integrator: SpectrumPowerIntegrator | None = None,
) -> WaterfallPowerStatistics:
    engine = integrator or SpectrumPowerIntegrator()
    values: list[float] = []
    qualities: list[MeasurementQuality] = []
    warnings: list[MeasurementWarning] = []
    count = 0
    for frame in frames:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Расчёт отменён")
        count += 1
        result = engine.integrate(frame, start_hz, stop_hz)
        qualities.append(result.quality)
        warnings.extend(result.warnings)
        if result.power_mw is not None:
            values.append(result.power_mw)
    if not values:
        return WaterfallPowerStatistics(count, 0, None, None, None, None, None, MeasurementQuality.INVALID, _unique_warnings(warnings))
    linear = np.asarray(values)
    db = np.asarray(mw_to_dbm(linear))
    return WaterfallPowerStatistics(
        count, len(values), float(mw_to_dbm(np.mean(linear))), float(np.median(db)),
        float(np.min(db)), float(np.max(db)), float(np.std(db)),
        _worst_quality(*qualities), _unique_warnings(warnings),
    )
