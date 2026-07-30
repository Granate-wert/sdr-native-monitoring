from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Iterable

import numpy as np

from .spectrogram import SpectrogramRow


class PowerSemantics(StrEnum):
    POWER_PER_BIN = "power_per_bin"
    PSD_PER_HZ = "psd_per_hz"
    RBW_FILTERED_POWER = "rbw_filtered_power"
    INSTRUMENT_CHANNEL_POWER = "instrument_channel_power"
    UNKNOWN = "unknown"


class ChannelPowerMode(StrEnum):
    CURRENT_FRAME = "current_frame"
    SELECTED_INTERVAL_ALL_FRAMES = "selected_interval_all_frames"
    SELECTED_INTERVAL_ACTIVE_ONLY = "selected_interval_active_only"
    AUTOMATIC_ACTIVE_PERIODS = "automatic_active_periods"
    ENTIRE_RECORDING_ALL_FRAMES = "entire_recording_all_frames"
    ENTIRE_RECORDING_ACTIVE_ONLY = "entire_recording_active_only"
    SELECTED_EVENTS = "selected_events"


class FrameInclusion(StrEnum):
    ALL = "all"
    ACTIVE_ONLY = "active_only"
    INACTIVE_ONLY = "inactive_only"
    MANUAL_MASK = "manual_mask"


class ActivityThresholdMode(StrEnum):
    ABSOLUTE = "absolute"
    AUTO_NOISE_RELATIVE = "auto_noise_relative"
    AUTO_ROBUST_STATISTICS = "auto_robust_statistics"
    MANUAL_NOISE_REGION = "manual_noise_region"
    PERCENTILE = "percentile"


class SmoothingMode(StrEnum):
    NONE = "none"
    MEDIAN = "median"
    MOVING_AVERAGE = "moving_average"
    EXPONENTIAL = "exponential"


class CalculationQuality(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    LIMITED = "limited"


class EstimateConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ManualOverride(IntEnum):
    FORCE_INACTIVE = 0
    FORCE_ACTIVE = 1
    AUTO = 2


@dataclass(frozen=True, slots=True)
class ActivityDetectionConfig:
    enabled: bool = True
    threshold_mode: ActivityThresholdMode = ActivityThresholdMode.AUTO_NOISE_RELATIVE
    absolute_threshold_dbm: float | None = None
    threshold_on_offset_db: float = 10.0
    threshold_off_offset_db: float = 6.0
    robust_sigma_multiplier: float = 6.0
    idle_percentile: float = 20.0
    smoothing_mode: SmoothingMode = SmoothingMode.MEDIAN
    smoothing_window_frames: int = 3
    min_active_frames: int = 2
    min_inactive_frames: int = 2
    min_active_duration_s: float | None = None
    min_inactive_duration_s: float | None = None
    max_gap_frames: int = 1
    max_gap_duration_s: float | None = None
    merge_gap_frames: int = 1
    merge_gap_duration_s: float | None = None
    manual_noise_start_s: float | None = None
    manual_noise_stop_s: float | None = None
    use_hysteresis: bool = True


@dataclass(frozen=True, slots=True)
class ChannelPowerRequest:
    session_id: str
    trace_id: str
    frequency_start_hz: float
    frequency_stop_hz: float
    time_start_s: float | None = None
    time_stop_s: float | None = None
    mode: ChannelPowerMode = ChannelPowerMode.ENTIRE_RECORDING_ACTIVE_ONLY
    frame_inclusion: FrameInclusion = FrameInclusion.ALL
    activity_config: ActivityDetectionConfig | None = None
    subtract_idle_power: bool = True
    include_partial_bins: bool = True
    selected_frame_index: int | None = None
    selected_event_ids: tuple[str, ...] | None = None
    power_semantics: PowerSemantics = PowerSemantics.UNKNOWN
    rbw_hz: float | None = None
    enbw_hz: float | None = None
    source_revision: str = ""


@dataclass(frozen=True, slots=True)
class ChannelPowerFrame:
    frame_index: int
    timestamp_s: float
    power_mw: float
    power_dbm: float
    is_valid: bool


@dataclass(slots=True)
class ChannelPowerSeries:
    frame_indices: np.ndarray
    timestamps_s: np.ndarray
    power_mw: np.ndarray
    power_dbm: np.ndarray
    valid_mask: np.ndarray
    approximate: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def frame_count(self) -> int:
        return int(self.frame_indices.size)


@dataclass(frozen=True, slots=True)
class IdlePowerEstimate:
    mean_idle_mw: float
    mean_idle_dbm: float
    median_idle_dbm: float
    percentile_idle_dbm: float
    mad_db: float
    standard_deviation_db: float
    sample_count: int
    confidence: EstimateConfidence


@dataclass(slots=True)
class ActivityDetectionResult:
    smoothed_power_dbm: np.ndarray
    automatic_activity_mask: np.ndarray
    manual_override_mask: np.ndarray
    effective_activity_mask: np.ndarray
    threshold_on_dbm: float | None
    threshold_off_dbm: float | None
    idle_estimate: IdlePowerEstimate | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    event_id: str
    start_frame_index: int
    stop_frame_index: int
    start_time_s: float
    stop_time_s: float
    duration_s: float
    active_frame_count: int
    mean_power_mw: float
    mean_power_dbm: float
    max_power_mw: float
    max_power_dbm: float
    max_power_time_s: float
    min_power_mw: float
    min_power_dbm: float
    integrated_energy_mj: float | None
    frequency_start_hz: float
    frequency_stop_hz: float
    manually_edited: bool = False


@dataclass(slots=True)
class TimeGatedChannelPowerResult:
    request: ChannelPowerRequest
    series: ChannelPowerSeries
    activity: ActivityDetectionResult
    frame_count_total: int
    frame_count_valid: int
    frame_count_active: int
    frame_count_inactive: int
    selected_duration_s: float
    active_duration_s: float
    inactive_duration_s: float
    duty_cycle_percent: float
    active_mean_power_mw: float | None
    active_mean_power_dbm: float | None
    long_term_mean_power_mw: float | None
    long_term_mean_power_dbm: float | None
    idle_mean_power_mw: float | None
    idle_mean_power_dbm: float | None
    noise_corrected_active_power_mw: float | None
    noise_corrected_active_power_dbm: float | None
    maximum_frame_power_mw: float | None
    maximum_frame_power_dbm: float | None
    maximum_frame_time_s: float | None
    minimum_active_power_mw: float | None
    minimum_active_power_dbm: float | None
    median_active_power_dbm: float | None
    active_power_std_db: float | None
    events: tuple[ActivityEvent, ...]
    calculation_quality: CalculationQuality
    warnings: tuple[str, ...] = ()


def dbm_to_mw(values: np.ndarray | float) -> np.ndarray:
    with np.errstate(over="ignore", invalid="ignore"):
        return np.power(10.0, np.asarray(values, dtype=np.float64) / 10.0)


def mw_to_dbm(values: np.ndarray | float) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(np.asarray(values, dtype=np.float64))


def _bin_edges(frequencies_hz: np.ndarray) -> np.ndarray:
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    if frequencies.size == 1:
        return np.array([frequencies[0] - 0.5, frequencies[0] + 0.5])
    mid = (frequencies[:-1] + frequencies[1:]) / 2.0
    return np.r_[frequencies[0] - (mid[0] - frequencies[0]), mid,
                 frequencies[-1] + (frequencies[-1] - mid[-1])]


class ChannelPowerService:
    @staticmethod
    def _integration_weights(
        frequencies_hz: np.ndarray,
        request: ChannelPowerRequest,
    ) -> tuple[np.ndarray, np.ndarray, bool, tuple[str, ...]]:
        frequencies = np.asarray(frequencies_hz, dtype=np.float64)
        original_indices = np.flatnonzero(np.isfinite(frequencies))
        frequencies = frequencies[original_indices]
        if not frequencies.size:
            return np.empty(0, dtype=np.int64), np.empty(0), True, ("Нет частотной сетки",)
        order = np.argsort(frequencies)
        frequencies = frequencies[order]
        original_indices = original_indices[order]
        edges = _bin_edges(frequencies)
        widths = np.maximum(edges[1:] - edges[:-1], np.finfo(float).eps)
        low, high = sorted((request.frequency_start_hz, request.frequency_stop_hz))
        overlaps = np.maximum(0.0, np.minimum(edges[1:], high) - np.maximum(edges[:-1], low))
        if not request.include_partial_bins:
            overlaps = np.where((frequencies >= low) & (frequencies <= high), widths, 0.0)
        selected = overlaps > 0.0
        if not selected.any():
            return (
                np.empty(0, dtype=np.int64), np.empty(0), True,
                ("Полоса находится вне частотной сетки",),
            )
        approximate = False
        warnings: list[str] = []
        if request.power_semantics == PowerSemantics.PSD_PER_HZ:
            weights = overlaps
        elif request.power_semantics == PowerSemantics.RBW_FILTERED_POWER:
            effective_bandwidth = request.enbw_hz or request.rbw_hz
            if not effective_bandwidth or effective_bandwidth <= 0:
                effective_bandwidth = float(np.median(widths))
                approximate = True
                warnings.append("ENBW/RBW неизвестна; использован частотный шаг")
            weights = overlaps / effective_bandwidth
        elif request.power_semantics == PowerSemantics.INSTRUMENT_CHANNEL_POWER:
            weights = np.ones_like(overlaps)
            approximate = True
            warnings.append("Приборное Channel Power нельзя повторно интегрировать как спектр")
        else:
            weights = overlaps / widths
            if request.power_semantics == PowerSemantics.UNKNOWN:
                approximate = True
                warnings.append("Нормализация мощности источника неизвестна")
        return (
            original_indices[selected].astype(np.int64, copy=False),
            weights[selected].astype(np.float64, copy=False),
            approximate,
            tuple(warnings),
        )

    def frame_power(
        self,
        frequencies_hz: np.ndarray,
        values_db: np.ndarray,
        start_hz: float,
        stop_hz: float,
        semantics: PowerSemantics,
        rbw_hz: float | None = None,
        enbw_hz: float | None = None,
        include_partial_bins: bool = True,
    ) -> tuple[float, float, bool, tuple[str, ...]]:
        frequencies = np.asarray(frequencies_hz, dtype=np.float64)
        values = np.asarray(values_db, dtype=np.float64)
        count = min(frequencies.size, values.size)
        finite = np.isfinite(frequencies[:count]) & np.isfinite(values[:count])
        frequencies, values = frequencies[:count][finite], values[:count][finite]
        if frequencies.size == 0:
            return np.nan, np.nan, True, ("Нет валидных отсчётов",)
        if frequencies[0] > frequencies[-1]:
            frequencies, values = frequencies[::-1], values[::-1]
        low, high = sorted((float(start_hz), float(stop_hz)))
        edges = _bin_edges(frequencies)
        widths = np.maximum(edges[1:] - edges[:-1], np.finfo(float).eps)
        overlaps = np.maximum(0.0, np.minimum(edges[1:], high) - np.maximum(edges[:-1], low))
        if not include_partial_bins:
            overlaps = np.where((frequencies >= low) & (frequencies <= high), widths, 0.0)
        selected = overlaps > 0.0
        if not selected.any():
            return np.nan, np.nan, True, ("Полоса находится вне частотной сетки",)
        linear = dbm_to_mw(values)
        approximate = False
        warnings: list[str] = []
        if semantics == PowerSemantics.PSD_PER_HZ:
            contributions = linear * overlaps
        elif semantics == PowerSemantics.RBW_FILTERED_POWER:
            effective_bandwidth = enbw_hz or rbw_hz
            if not effective_bandwidth or effective_bandwidth <= 0:
                effective_bandwidth = float(np.median(widths))
                approximate = True
                warnings.append("ENBW/RBW неизвестна; использован частотный шаг")
            contributions = linear * overlaps / effective_bandwidth
        elif semantics == PowerSemantics.INSTRUMENT_CHANNEL_POWER:
            contributions = linear
            approximate = True
            warnings.append("Приборное Channel Power нельзя повторно интегрировать как спектр")
        else:
            contributions = linear * overlaps / widths
            if semantics == PowerSemantics.UNKNOWN:
                approximate = True
                warnings.append("Нормализация мощности источника неизвестна")
        power_mw = float(np.sum(contributions[selected]))
        power_dbm = float(mw_to_dbm(power_mw)) if power_mw > 0 else np.nan
        return power_mw, power_dbm, approximate, tuple(warnings)

    def build_series(
        self,
        rows: Iterable[SpectrogramRow],
        frequencies_hz: np.ndarray,
        request: ChannelPowerRequest,
        cancel: threading.Event | None = None,
    ) -> ChannelPowerSeries:
        indices: list[int] = []
        timestamps: list[float] = []
        powers_mw: list[float] = []
        powers_dbm: list[float] = []
        valid: list[bool] = []
        bin_indices, weights, approximate, initial_warnings = self._integration_weights(
            frequencies_hz, request
        )
        warnings: set[str] = set(initial_warnings)
        for sequential_index, row in enumerate(rows):
            if cancel is not None and cancel.is_set():
                from .spectrogram import OperationCancelled
                raise OperationCancelled("Расчёт Channel Power отменён")
            if bin_indices.size and bin_indices[-1] < row.values.size:
                selected_indices = bin_indices
                selected_weights = weights
            else:
                usable = bin_indices < row.values.size
                selected_indices = bin_indices[usable]
                selected_weights = weights[usable]
            selected_values = np.asarray(row.values[selected_indices], dtype=np.float64)
            finite = np.isfinite(selected_values)
            if finite.any():
                linear = np.power(10.0, selected_values[finite] / 10.0)
                power_mw = float(np.dot(linear, selected_weights[finite]))
                power_dbm = float(mw_to_dbm(power_mw)) if power_mw > 0 else np.nan
            else:
                power_mw = power_dbm = np.nan
            indices.append(sequential_index)
            timestamps.append(row.timestamp)
            powers_mw.append(power_mw)
            powers_dbm.append(power_dbm)
            valid.append(bool(np.isfinite(power_mw) and power_mw >= 0.0))
        order = np.argsort(np.where(np.isfinite(timestamps), timestamps, np.inf))
        return ChannelPowerSeries(
            np.asarray(indices, dtype=np.int64)[order],
            np.asarray(timestamps, dtype=np.float64)[order],
            np.asarray(powers_mw, dtype=np.float64)[order],
            np.asarray(powers_dbm, dtype=np.float32)[order],
            np.asarray(valid, dtype=bool)[order],
            approximate,
            tuple(sorted(warnings)),
        )


class ActivityDetectionService:
    def smooth(self, values_dbm: np.ndarray, config: ActivityDetectionConfig) -> np.ndarray:
        values = np.asarray(values_dbm, dtype=np.float64)
        window = max(1, int(config.smoothing_window_frames))
        if config.smoothing_mode == SmoothingMode.NONE or window <= 1:
            return values.copy()
        if window % 2 == 0:
            window += 1
        pad = window // 2
        padded = np.pad(values, pad, mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, window)
        if config.smoothing_mode == SmoothingMode.MEDIAN:
            return np.nanmedian(windows, axis=1)
        if config.smoothing_mode == SmoothingMode.MOVING_AVERAGE:
            return np.asarray(mw_to_dbm(np.nanmean(dbm_to_mw(windows), axis=1)))
        alpha = 2.0 / (window + 1.0)
        output = values.copy()
        linear = dbm_to_mw(values)
        state = np.nan
        for index, value in enumerate(linear):
            if not np.isfinite(value):
                continue
            state = value if not np.isfinite(state) else alpha * value + (1.0 - alpha) * state
            output[index] = float(mw_to_dbm(state))
        return output

    def estimate_idle(
        self,
        power_dbm: np.ndarray,
        valid_mask: np.ndarray,
        percentile: float = 20.0,
        manual_mask: np.ndarray | None = None,
    ) -> IdlePowerEstimate | None:
        valid = valid_mask & np.isfinite(power_dbm)
        if manual_mask is not None:
            valid &= manual_mask
        values = np.asarray(power_dbm[valid], dtype=np.float64)
        if values.size == 0:
            return None
        cutoff = float(np.percentile(values, np.clip(percentile, 1.0, 80.0)))
        subset = values[values <= cutoff]
        if subset.size == 0:
            subset = values
        baseline = float(np.median(subset))
        mad = float(np.median(np.abs(subset - baseline)))
        mean_mw = float(np.mean(dbm_to_mw(subset)))
        spread = float(np.ptp(values)) if values.size else 0.0
        confidence = (
            EstimateConfidence.HIGH if values.size >= 20 and subset.size >= 5 and spread >= 6.0
            else EstimateConfidence.MEDIUM if values.size >= 10 and subset.size >= 3
            else EstimateConfidence.LOW
        )
        return IdlePowerEstimate(
            mean_mw,
            float(mw_to_dbm(mean_mw)),
            baseline,
            cutoff,
            mad,
            float(np.std(subset)),
            int(subset.size),
            confidence,
        )

    def detect(
        self,
        series: ChannelPowerSeries,
        config: ActivityDetectionConfig,
        manual_override: np.ndarray | None = None,
    ) -> ActivityDetectionResult:
        smoothed = self.smooth(series.power_dbm, config)
        manual_noise_mask = None
        if config.manual_noise_start_s is not None and config.manual_noise_stop_s is not None:
            low, high = sorted((config.manual_noise_start_s, config.manual_noise_stop_s))
            manual_noise_mask = (series.timestamps_s >= low) & (series.timestamps_s <= high)
        idle = self.estimate_idle(
            series.power_dbm, series.valid_mask, config.idle_percentile, manual_noise_mask
        )
        warnings: list[str] = []
        threshold_on: float | None = None
        threshold_off: float | None = None
        if not config.enabled:
            automatic = series.valid_mask.copy()
        elif config.threshold_mode == ActivityThresholdMode.ABSOLUTE:
            threshold_on = config.absolute_threshold_dbm
            threshold_off = (
                threshold_on - max(0.1, config.threshold_on_offset_db - config.threshold_off_offset_db)
                if threshold_on is not None else None
            )
        elif config.threshold_mode == ActivityThresholdMode.PERCENTILE:
            candidates = smoothed[series.valid_mask & np.isfinite(smoothed)]
            if candidates.size:
                percentile = float(np.clip(100.0 - config.idle_percentile, 50.0, 99.9))
                threshold_on = float(np.percentile(candidates, percentile))
                threshold_off = threshold_on - max(
                    0.1, config.threshold_on_offset_db - config.threshold_off_offset_db
                )
        elif idle is not None:
            if config.threshold_mode == ActivityThresholdMode.AUTO_ROBUST_STATISTICS:
                sigma = max(1.4826 * idle.mad_db, 1.0)
                threshold_on = idle.median_idle_dbm + max(6.0, config.robust_sigma_multiplier * sigma)
                threshold_off = idle.median_idle_dbm + max(3.0, 4.0 * sigma)
            else:
                threshold_on = idle.median_idle_dbm + config.threshold_on_offset_db
                threshold_off = idle.median_idle_dbm + config.threshold_off_offset_db
        if config.threshold_mode == ActivityThresholdMode.MANUAL_NOISE_REGION and manual_noise_mask is None:
            warnings.append("Не выбран ручной шумовой интервал")
        if not config.enabled:
            pass
        elif threshold_on is None or threshold_off is None or not np.isfinite(threshold_on + threshold_off):
            warnings.append("Автоматический порог недоступен")
            automatic = np.zeros(series.frame_count, dtype=bool)
        else:
            if threshold_on <= threshold_off:
                threshold_on = threshold_off + 0.1
            automatic = self._hysteresis_mask(
                smoothed,
                series.valid_mask,
                threshold_on,
                threshold_off if config.use_hysteresis else threshold_on,
                config.min_active_frames,
                config.min_inactive_frames,
            )
            automatic = self._fill_short_gaps(
                automatic,
                max(config.max_gap_frames, config.merge_gap_frames),
            )
            automatic = self._filter_short_runs(automatic, config.min_active_frames)
            automatic = self._apply_duration_rules(automatic, series.timestamps_s, config)
        if config.enabled and (idle is None or idle.confidence == EstimateConfidence.LOW):
            warnings.append("Автоматическая оценка idle level имеет низкую уверенность")
        if manual_override is None or manual_override.size != series.frame_count:
            manual_override = np.full(series.frame_count, ManualOverride.AUTO, dtype=np.uint8)
        effective = automatic.copy()
        effective[manual_override == ManualOverride.FORCE_ACTIVE] = True
        effective[manual_override == ManualOverride.FORCE_INACTIVE] = False
        effective &= series.valid_mask
        return ActivityDetectionResult(
            smoothed.astype(np.float32), automatic, manual_override.astype(np.uint8), effective,
            threshold_on, threshold_off, idle, tuple(warnings),
        )

    @classmethod
    def _apply_duration_rules(
        cls,
        mask: np.ndarray,
        timestamps: np.ndarray,
        config: ActivityDetectionConfig,
    ) -> np.ndarray:
        result = mask.copy()
        if config.min_active_duration_s is not None:
            result = cls._filter_runs_by_duration(
                result, timestamps, config.min_active_duration_s, state=True
            )
        maximum_gap = max(
            value for value in (config.max_gap_duration_s, config.merge_gap_duration_s, 0.0)
            if value is not None
        )
        if maximum_gap > 0:
            result = cls._fill_gaps_by_duration(result, timestamps, maximum_gap)
        if config.min_inactive_duration_s is not None:
            result = cls._filter_runs_by_duration(
                result, timestamps, config.min_inactive_duration_s, state=False
            )
        return result

    @staticmethod
    def _run_duration(timestamps: np.ndarray, start: int, stop: int) -> float:
        if stop <= start:
            if timestamps.size > 1:
                deltas = np.diff(timestamps)
                valid = deltas[np.isfinite(deltas) & (deltas > 0)]
                return float(np.median(valid)) if valid.size else 0.0
            return 0.0
        value = float(timestamps[stop] - timestamps[start])
        return value if np.isfinite(value) and value >= 0 else 0.0

    @classmethod
    def _filter_runs_by_duration(
        cls, mask: np.ndarray, timestamps: np.ndarray, minimum_s: float, state: bool
    ) -> np.ndarray:
        result = mask.copy()
        indices = np.flatnonzero(result == state)
        if not indices.size:
            return result
        starts = indices[np.r_[True, np.diff(indices) > 1]]
        stops = indices[np.r_[np.diff(indices) > 1, True]]
        for start, stop in zip(starts, stops):
            if cls._run_duration(timestamps, int(start), int(stop)) < max(0.0, minimum_s):
                result[int(start) : int(stop) + 1] = not state
        return result

    @classmethod
    def _fill_gaps_by_duration(
        cls, mask: np.ndarray, timestamps: np.ndarray, maximum_s: float
    ) -> np.ndarray:
        result = mask.copy()
        indices = np.flatnonzero(~result)
        if not indices.size:
            return result
        starts = indices[np.r_[True, np.diff(indices) > 1]]
        stops = indices[np.r_[np.diff(indices) > 1, True]]
        for start, stop in zip(starts, stops):
            if (
                start > 0
                and stop < result.size - 1
                and cls._run_duration(timestamps, int(start), int(stop)) <= maximum_s
            ):
                result[int(start) : int(stop) + 1] = True
        return result

    @staticmethod
    def _hysteresis_mask(
        values: np.ndarray,
        valid: np.ndarray,
        threshold_on: float,
        threshold_off: float,
        min_active: int,
        min_inactive: int,
    ) -> np.ndarray:
        count = values.size
        if not count:
            return np.zeros(0, dtype=bool)

        def confirmation_ends(mask: np.ndarray, minimum: int) -> np.ndarray:
            minimum = max(1, minimum)
            cumulative = np.r_[0, np.cumsum(mask, dtype=np.int64)]
            confirmed = cumulative[minimum:] - cumulative[:-minimum] == minimum
            return np.flatnonzero(confirmed) + minimum - 1

        finite_valid = valid & np.isfinite(values)
        active_minimum = max(1, min_active)
        inactive_minimum = max(1, min_inactive)
        on_ends = confirmation_ends(finite_valid & (values >= threshold_on), active_minimum)
        off_ends = confirmation_ends(~finite_valid | (values <= threshold_off), inactive_minimum)
        starts: list[int] = []
        stops: list[int] = []
        cursor = 0
        while cursor < count:
            on_position = int(np.searchsorted(on_ends, cursor))
            if on_position >= on_ends.size:
                break
            on_end = int(on_ends[on_position])
            start = on_end - active_minimum + 1
            off_position = int(np.searchsorted(off_ends, on_end + 1))
            if off_position >= off_ends.size:
                starts.append(start)
                stops.append(count)
                break
            off_end = int(off_ends[off_position])
            stop = off_end - inactive_minimum + 1
            starts.append(start)
            stops.append(max(start, stop))
            cursor = off_end + 1
        differences = np.zeros(count + 1, dtype=np.int32)
        if starts:
            np.add.at(differences, np.asarray(starts), 1)
            np.add.at(differences, np.asarray(stops), -1)
        return np.cumsum(differences[:-1]) > 0

    @staticmethod
    def _fill_short_gaps(mask: np.ndarray, maximum: int) -> np.ndarray:
        result = mask.copy()
        if maximum <= 0:
            return result
        false_indices = np.flatnonzero(~result)
        if not false_indices.size:
            return result
        starts = false_indices[np.r_[True, np.diff(false_indices) > 1]]
        stops = false_indices[np.r_[np.diff(false_indices) > 1, True]]
        for start, stop in zip(starts, stops):
            if start > 0 and stop < result.size - 1 and stop - start + 1 <= maximum:
                result[start : stop + 1] = True
        return result

    @staticmethod
    def _filter_short_runs(mask: np.ndarray, minimum: int) -> np.ndarray:
        result = mask.copy()
        true_indices = np.flatnonzero(result)
        if not true_indices.size:
            return result
        starts = true_indices[np.r_[True, np.diff(true_indices) > 1]]
        stops = true_indices[np.r_[np.diff(true_indices) > 1, True]]
        for start, stop in zip(starts, stops):
            if stop - start + 1 < max(1, minimum):
                result[start : stop + 1] = False
        return result


class BurstAnalysisService:
    @staticmethod
    def _durations(timestamps: np.ndarray) -> np.ndarray:
        if timestamps.size <= 1:
            return np.ones(timestamps.size, dtype=np.float64)
        differences = np.diff(timestamps)
        finite_positive = differences[np.isfinite(differences) & (differences > 0)]
        fallback = float(np.median(finite_positive)) if finite_positive.size else 1.0
        return np.r_[np.where(differences > 0, differences, fallback), fallback]

    def events(
        self,
        series: ChannelPowerSeries,
        activity: ActivityDetectionResult,
        frequency_start_hz: float,
        frequency_stop_hz: float,
    ) -> tuple[ActivityEvent, ...]:
        mask = activity.effective_activity_mask
        active_indices = np.flatnonzero(mask)
        if not active_indices.size:
            return ()
        starts = active_indices[np.r_[True, np.diff(active_indices) > 1]]
        stops = active_indices[np.r_[np.diff(active_indices) > 1, True]]
        durations = self._durations(series.timestamps_s)
        lengths = stops - starts + 1
        groups = np.repeat(np.arange(starts.size, dtype=np.int64), lengths)
        grouped_indices = active_indices
        grouped_power = series.power_mw[grouped_indices]
        grouped_duration = durations[grouped_indices]
        event_count = starts.size
        power_sum = np.zeros(event_count, dtype=np.float64)
        duration_sum = np.zeros(event_count, dtype=np.float64)
        energy_sum = np.zeros(event_count, dtype=np.float64)
        maximums = np.full(event_count, -np.inf, dtype=np.float64)
        minimums = np.full(event_count, np.inf, dtype=np.float64)
        np.add.at(power_sum, groups, grouped_power * grouped_duration)
        np.add.at(duration_sum, groups, grouped_duration)
        np.add.at(energy_sum, groups, grouped_power * grouped_duration)
        np.maximum.at(maximums, groups, grouped_power)
        np.minimum.at(minimums, groups, grouped_power)
        maximum_indices = np.full(event_count, series.frame_count, dtype=np.int64)
        maximum_candidates = grouped_power == maximums[groups]
        np.minimum.at(
            maximum_indices,
            groups[maximum_candidates],
            grouped_indices[maximum_candidates],
        )
        manual_flags = np.zeros(event_count, dtype=bool)
        np.logical_or.at(
            manual_flags,
            groups,
            activity.manual_override_mask[grouped_indices] != ManualOverride.AUTO,
        )
        result: list[ActivityEvent] = []
        for event_index, (start, stop) in enumerate(zip(starts, stops)):
            mean_mw = float(power_sum[event_index] / duration_sum[event_index])
            max_mw = float(maximums[event_index])
            min_mw = float(minimums[event_index])
            duration = float(duration_sum[event_index])
            maximum_local = int(maximum_indices[event_index])
            result.append(
                ActivityEvent(
                    f"E{int(start) + 1}-{int(stop) + 1}", int(start), int(stop),
                    float(series.timestamps_s[start]), float(series.timestamps_s[stop]), duration,
                    int(stop - start + 1), mean_mw, float(mw_to_dbm(mean_mw)),
                    max_mw, float(mw_to_dbm(max_mw)), float(series.timestamps_s[maximum_local]),
                    min_mw, float(mw_to_dbm(min_mw)),
                    float(energy_sum[event_index]),
                    frequency_start_hz, frequency_stop_hz, bool(manual_flags[event_index]),
                )
            )
        return tuple(result)

    def summarize(
        self,
        request: ChannelPowerRequest,
        series: ChannelPowerSeries,
        activity: ActivityDetectionResult,
    ) -> TimeGatedChannelPowerResult:
        selection = series.valid_mask.copy()
        if request.mode == ChannelPowerMode.CURRENT_FRAME and request.selected_frame_index is not None:
            selection[:] = False
            if 0 <= request.selected_frame_index < selection.size:
                selection[request.selected_frame_index] = series.valid_mask[request.selected_frame_index]
        elif request.time_start_s is not None and request.time_stop_s is not None:
            low, high = sorted((request.time_start_s, request.time_stop_s))
            selection &= (series.timestamps_s >= low) & (series.timestamps_s <= high)
        active = selection & activity.effective_activity_mask
        inactive = selection & ~activity.effective_activity_mask
        measurement_selection = selection.copy()
        if request.frame_inclusion == FrameInclusion.ACTIVE_ONLY:
            measurement_selection &= activity.effective_activity_mask
        elif request.frame_inclusion == FrameInclusion.INACTIVE_ONLY:
            measurement_selection &= ~activity.effective_activity_mask
        elif request.frame_inclusion == FrameInclusion.MANUAL_MASK:
            measurement_selection &= activity.manual_override_mask != ManualOverride.AUTO
        durations = self._durations(series.timestamps_s)

        def mean(mask: np.ndarray) -> tuple[float | None, float | None]:
            values = series.power_mw[mask]
            weights = durations[mask]
            finite = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
            values = values[finite]
            weights = weights[finite]
            if not values.size:
                return None, None
            value = float(np.average(values, weights=weights))
            return value, float(mw_to_dbm(value))

        active_mw, active_dbm = mean(active)
        long_mw, long_dbm = mean(measurement_selection)
        idle_mw, idle_dbm = mean(inactive)
        corrected_mw = None
        corrected_dbm = None
        if request.subtract_idle_power and active_mw is not None and idle_mw is not None:
            difference = active_mw - idle_mw
            if difference > 0:
                corrected_mw, corrected_dbm = difference, float(mw_to_dbm(difference))
        selected_duration = float(np.sum(durations[selection]))
        active_duration = float(np.sum(durations[active]))
        inactive_duration = float(np.sum(durations[inactive]))
        duty = active_duration / selected_duration * 100.0 if selected_duration > 0 else 0.0
        selected_values = series.power_mw[measurement_selection]
        maximum_mw: float | None
        maximum_dbm: float | None
        maximum_time: float | None
        if selected_values.size and np.isfinite(selected_values).any():
            maximum_index = int(
                np.nanargmax(np.where(measurement_selection, series.power_mw, np.nan))
            )
            maximum_mw = float(series.power_mw[maximum_index])
            maximum_dbm = float(mw_to_dbm(maximum_mw))
            maximum_time = float(series.timestamps_s[maximum_index])
        else:
            maximum_mw = maximum_dbm = maximum_time = None
        active_values_dbm = series.power_dbm[active & np.isfinite(series.power_dbm)]
        events = self.events(
            series, activity, request.frequency_start_hz, request.frequency_stop_hz
        )
        warnings = list(series.warnings) + list(activity.warnings)
        quality = CalculationQuality.APPROXIMATE if series.approximate else CalculationQuality.EXACT
        if not np.any(selection):
            quality = CalculationQuality.LIMITED
            warnings.append("Выбранный временной диапазон не содержит валидных кадров")
        return TimeGatedChannelPowerResult(
            request, series, activity, series.frame_count,
            int(np.count_nonzero(measurement_selection)),
            int(np.count_nonzero(active)), int(np.count_nonzero(inactive)),
            selected_duration, active_duration, inactive_duration, duty,
            active_mw, active_dbm, long_mw, long_dbm, idle_mw, idle_dbm,
            corrected_mw, corrected_dbm, maximum_mw, maximum_dbm, maximum_time,
            float(np.min(series.power_mw[active])) if np.any(active) else None,
            float(np.min(series.power_dbm[active])) if np.any(active) else None,
            float(np.median(active_values_dbm)) if active_values_dbm.size else None,
            float(np.std(active_values_dbm)) if active_values_dbm.size else None,
            events, quality, tuple(dict.fromkeys(warnings)),
        )


class ChannelPowerSeriesCache:
    def __init__(self, maximum_entries: int = 12) -> None:
        self.maximum_entries = maximum_entries
        self._items: OrderedDict[tuple[object, ...], ChannelPowerSeries] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def key(request: ChannelPowerRequest) -> tuple[object, ...]:
        return (
            request.session_id,
            request.trace_id,
            request.source_revision,
            round(request.frequency_start_hz, 6),
            round(request.frequency_stop_hz, 6),
            request.power_semantics,
            request.rbw_hz,
            request.enbw_hz,
            request.include_partial_bins,
        )

    def get(self, request: ChannelPowerRequest) -> ChannelPowerSeries | None:
        key = self.key(request)
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, request: ChannelPowerRequest, series: ChannelPowerSeries) -> None:
        key = self.key(request)
        with self._lock:
            self._items[key] = series
            self._items.move_to_end(key)
            while len(self._items) > self.maximum_entries:
                self._items.popitem(last=False)


class TimeGatedChannelPowerService:
    def __init__(self) -> None:
        self.channel_power = ChannelPowerService()
        self.activity_detection = ActivityDetectionService()
        self.burst_analysis = BurstAnalysisService()
        self.cache = ChannelPowerSeriesCache()

    def analyze(
        self,
        request: ChannelPowerRequest,
        frequencies_hz: np.ndarray,
        rows: Iterable[SpectrogramRow] | None,
        manual_override: np.ndarray | None = None,
        cancel: threading.Event | None = None,
    ) -> TimeGatedChannelPowerResult:
        series = self.cache.get(request)
        if series is None:
            if rows is None:
                raise ValueError("Временной ряд отсутствует в кэше, требуется источник кадров")
            series = self.channel_power.build_series(rows, frequencies_hz, request, cancel)
            self.cache.put(request, series)
        config = request.activity_config or ActivityDetectionConfig()
        activity = self.activity_detection.detect(series, config, manual_override)
        return self.burst_analysis.summarize(request, series, activity)
