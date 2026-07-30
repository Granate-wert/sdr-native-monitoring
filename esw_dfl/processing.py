from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .domain import SpectrumTrace


@dataclass(frozen=True, slots=True)
class ChannelPowerResult:
    start_hz: float
    stop_hz: float
    integrated_dbm: float
    mean_density_dbm_hz: float
    peak_dbm: float
    mean_level_dbm: float
    minimum_dbm: float
    peak_frequency_hz: float
    approximate: bool


@dataclass(frozen=True, slots=True)
class OccupiedBandwidthResult:
    lower_hz: float
    upper_hz: float
    bandwidth_hz: float
    center_hz: float
    total_power_dbm: float


@dataclass(frozen=True, slots=True)
class AcprChannel:
    name: str
    center_hz: float
    bandwidth_hz: float
    power_dbm: float
    aclr_db: float | None


@dataclass(frozen=True, slots=True)
class BandwidthResult:
    peak_frequency_hz: float
    peak_dbm: float
    left_hz: float
    right_hz: float
    bandwidth_hz: float
    asymmetry_hz: float


@dataclass(frozen=True, slots=True)
class NoiseFloorResult:
    mean_dbm: float
    median_dbm: float
    std_db: float
    minimum_dbm: float
    maximum_dbm: float
    density_dbm_hz: float | None
    sample_count: int


@dataclass(frozen=True, slots=True)
class SnrResult:
    signal_power_dbm: float
    noise_power_dbm: float
    snr_db: float
    signal_band: tuple[float, float]
    noise_band: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class HarmonicResult:
    order: int
    expected_frequency_hz: float
    measured_frequency_hz: float
    level_dbm: float
    relative_dbc: float


def dbm_to_mw(values_dbm: np.ndarray | float) -> np.ndarray:
    values = np.asarray(values_dbm, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        return np.power(10.0, values / 10.0)


def mw_to_dbm(values_mw: np.ndarray | float) -> np.ndarray:
    values = np.asarray(values_mw, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10.0 * np.log10(values)


def _ordered_trace(trace: SpectrumTrace) -> tuple[np.ndarray, np.ndarray]:
    frequencies = trace.frequencies_hz
    values = np.asarray(trace.power_values, dtype=np.float64)
    count = min(frequencies.size, values.size)
    finite = np.isfinite(frequencies[:count]) & np.isfinite(values[:count])
    frequencies = frequencies[:count][finite]
    values = values[:count][finite]
    if frequencies.size and frequencies[0] > frequencies[-1]:
        frequencies = frequencies[::-1]
        values = values[::-1]
    return frequencies, values


def _bin_edges(frequencies: np.ndarray) -> np.ndarray:
    if frequencies.size == 0:
        return np.empty(0, dtype=np.float64)
    if frequencies.size == 1:
        return np.array([frequencies[0] - 0.5, frequencies[0] + 0.5])
    midpoints = (frequencies[:-1] + frequencies[1:]) / 2.0
    return np.concatenate(
        ([frequencies[0] - (midpoints[0] - frequencies[0])], midpoints,
         [frequencies[-1] + (frequencies[-1] - midpoints[-1])])
    )


def _band_power(
    trace: SpectrumTrace, start_hz: float, stop_hz: float
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    start_hz, stop_hz = sorted((float(start_hz), float(stop_hz)))
    frequencies, values = _ordered_trace(trace)
    if frequencies.size == 0 or stop_hz <= start_hz:
        return float("nan"), np.empty(0), np.empty(0), np.empty(0)
    edges = _bin_edges(frequencies)
    overlaps = np.maximum(
        0.0, np.minimum(edges[1:], stop_hz) - np.maximum(edges[:-1], start_hz)
    )
    selected = overlaps > 0.0
    if not selected.any():
        return float("nan"), np.empty(0), np.empty(0), np.empty(0)
    widths = np.maximum(edges[1:] - edges[:-1], np.finfo(float).eps)
    linear = dbm_to_mw(values)
    if trace.unit.casefold() in {"dbm/hz", "dbm/Hz".casefold()}:
        weighted = linear * overlaps
    else:
        weighted = linear * (overlaps / widths)
    total_mw = float(np.sum(weighted[selected]))
    return float(mw_to_dbm(total_mw)), frequencies[selected], values[selected], overlaps[selected]


def channel_power(trace: SpectrumTrace, start_hz: float, stop_hz: float) -> ChannelPowerResult:
    integrated, frequencies, values, overlaps = _band_power(trace, start_hz, stop_hz)
    if values.size == 0:
        raise ValueError("В выбранной полосе нет конечных отсчётов")
    peak_index = int(np.argmax(values))
    bandwidth = abs(float(stop_hz) - float(start_hz))
    density = float(integrated - 10.0 * np.log10(bandwidth)) if bandwidth > 0 else np.nan
    mean_level = float(mw_to_dbm(np.mean(dbm_to_mw(values))))
    approximate = trace.rbw_hz is None or not trace.detector
    return ChannelPowerResult(
        min(start_hz, stop_hz), max(start_hz, stop_hz), integrated, density,
        float(values[peak_index]), mean_level, float(np.min(values)),
        float(frequencies[peak_index]), approximate,
    )


def occupied_bandwidth(trace: SpectrumTrace, fraction: float = 0.99) -> OccupiedBandwidthResult:
    if not 0.0 < fraction <= 1.0:
        raise ValueError("Доля мощности должна быть в диапазоне (0, 1]")
    frequencies, values = _ordered_trace(trace)
    if frequencies.size == 0:
        raise ValueError("Трасса не содержит конечных отсчётов")
    power = dbm_to_mw(values)
    total = float(np.sum(power))
    if total <= 0.0 or not np.isfinite(total):
        raise ValueError("Полная мощность трассы не определена")
    cumulative = np.cumsum(power)
    tail = total * (1.0 - fraction) / 2.0
    low_index = min(int(np.searchsorted(cumulative, tail)), frequencies.size - 1)
    high_index = min(int(np.searchsorted(cumulative, total - tail)), frequencies.size - 1)
    low, high = float(frequencies[low_index]), float(frequencies[high_index])
    return OccupiedBandwidthResult(
        low, high, high - low, (low + high) / 2.0, float(mw_to_dbm(total))
    )


def acpr(
    trace: SpectrumTrace,
    center_hz: float,
    main_bandwidth_hz: float,
    offset_hz: float,
    adjacent_bandwidth_hz: float | None = None,
    channel_count: int = 1,
) -> list[AcprChannel]:
    if main_bandwidth_hz <= 0 or offset_hz <= 0 or channel_count < 1:
        raise ValueError("Полоса, смещение и число каналов должны быть положительными")
    adjacent_bandwidth_hz = adjacent_bandwidth_hz or main_bandwidth_hz
    main = channel_power(
        trace, center_hz - main_bandwidth_hz / 2.0, center_hz + main_bandwidth_hz / 2.0
    )
    result = [AcprChannel("Main", center_hz, main_bandwidth_hz, main.integrated_dbm, None)]
    for order in range(1, channel_count + 1):
        for side, sign in (("Lower", -1), ("Upper", 1)):
            adjacent_center = center_hz + sign * offset_hz * order
            adjacent = channel_power(
                trace,
                adjacent_center - adjacent_bandwidth_hz / 2.0,
                adjacent_center + adjacent_bandwidth_hz / 2.0,
            )
            result.append(
                AcprChannel(
                    f"{side} {order}", adjacent_center, adjacent_bandwidth_hz,
                    adjacent.integrated_dbm, main.integrated_dbm - adjacent.integrated_dbm,
                )
            )
    return result


def peak_search(
    trace: SpectrumTrace,
    minimum_level_dbm: float = -np.inf,
    minimum_distance_hz: float = 0.0,
    limit: int = 20,
) -> list[tuple[float, float, int]]:
    frequencies, values = _ordered_trace(trace)
    return peak_search_values(
        frequencies,
        values,
        minimum_level_dbm=minimum_level_dbm,
        minimum_distance_hz=minimum_distance_hz,
        limit=limit,
    )


def peak_search_values(
    frequencies_hz: np.ndarray,
    values_dbm: np.ndarray,
    minimum_level_dbm: float = -np.inf,
    minimum_distance_hz: float = 0.0,
    limit: int = 20,
) -> list[tuple[float, float, int]]:
    """Find local maxima in the exact values currently presented to the user.

    Flat maxima are represented by their centre sample instead of an arbitrary
    plateau edge.  Non-finite samples split the trace into independent runs.
    """
    all_frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
    all_values = np.asarray(values_dbm, dtype=np.float64).reshape(-1)
    usable = min(all_frequencies.size, all_values.size)
    frequencies = all_frequencies[:usable]
    values = all_values[:usable]
    if values.size == 0:
        return []
    candidates: list[int] = []
    index = 0
    while index < values.size:
        if not np.isfinite(values[index]):
            index += 1
            continue
        plateau_stop = index
        while (
            plateau_stop + 1 < values.size
            and np.isfinite(values[plateau_stop + 1])
            and values[plateau_stop + 1] == values[index]
        ):
            plateau_stop += 1
        level = values[index]
        left = values[index - 1] if index > 0 and np.isfinite(values[index - 1]) else -np.inf
        right = (
            values[plateau_stop + 1]
            if plateau_stop + 1 < values.size and np.isfinite(values[plateau_stop + 1])
            else -np.inf
        )
        if level >= minimum_level_dbm and level >= left and level >= right and (level > left or level > right):
            candidates.append((index + plateau_stop) // 2)
        index = plateau_stop + 1
    candidate_array = np.asarray(candidates, dtype=np.int64)
    if candidate_array.size == 0:
        return []
    ordered = candidate_array[np.argsort(values[candidate_array], kind="stable")[::-1]]
    selected: list[int] = []
    for index in ordered:
        if all(abs(frequencies[index] - frequencies[other]) >= minimum_distance_hz for other in selected):
            selected.append(int(index))
        if len(selected) >= max(1, limit):
            break
    return [(float(frequencies[i]), float(values[i]), i) for i in selected]


def x_db_bandwidth(
    trace: SpectrumTrace, drop_db: float = 3.0, peak_frequency_hz: float | None = None
) -> BandwidthResult:
    frequencies, values = _ordered_trace(trace)
    if frequencies.size < 2 or drop_db <= 0:
        raise ValueError("Недостаточно данных или неверный уровень X dB")
    peak = int(np.argmax(values)) if peak_frequency_hz is None else int(
        np.argmin(np.abs(frequencies - peak_frequency_hz))
    )
    threshold = values[peak] - drop_db

    def crossing(i1: int, i2: int) -> float:
        x1, x2, y1, y2 = frequencies[i1], frequencies[i2], values[i1], values[i2]
        if y2 == y1:
            return float((x1 + x2) / 2.0)
        return float(x1 + (threshold - y1) * (x2 - x1) / (y2 - y1))

    left_candidates = np.flatnonzero(values[:peak] < threshold)
    right_candidates = np.flatnonzero(values[peak + 1 :] < threshold)
    left = crossing(int(left_candidates[-1]), int(left_candidates[-1] + 1)) if left_candidates.size else float(frequencies[0])
    right_index = int(peak + 1 + right_candidates[0]) if right_candidates.size else frequencies.size - 1
    right = crossing(right_index - 1, right_index) if right_candidates.size else float(frequencies[-1])
    peak_frequency = float(frequencies[peak])
    return BandwidthResult(
        peak_frequency, float(values[peak]), left, right, right - left,
        (right - peak_frequency) - (peak_frequency - left),
    )


def noise_floor(
    trace: SpectrumTrace,
    start_hz: float | None = None,
    stop_hz: float | None = None,
    percentile: float = 50.0,
    exclude_peaks_db: float = 8.0,
) -> NoiseFloorResult:
    frequencies, values = _ordered_trace(trace)
    if start_hz is not None and stop_hz is not None:
        low, high = sorted((start_hz, stop_hz))
        values = values[(frequencies >= low) & (frequencies <= high)]
    if values.size == 0:
        raise ValueError("Нет данных для оценки шума")
    baseline = float(np.percentile(values, percentile))
    noise = values[values <= baseline + exclude_peaks_db]
    if noise.size == 0:
        noise = values
    mean = float(mw_to_dbm(np.mean(dbm_to_mw(noise))))
    density = mean - 10.0 * np.log10(trace.rbw_hz) if trace.rbw_hz and trace.rbw_hz > 0 else None
    return NoiseFloorResult(
        mean, float(np.median(noise)), float(np.std(noise)), float(np.min(noise)),
        float(np.max(noise)), density, int(noise.size),
    )


def snr(
    trace: SpectrumTrace,
    signal_band: tuple[float, float],
    noise_band: tuple[float, float] | None = None,
) -> SnrResult:
    signal_dbm, *_ = _band_power(trace, *signal_band)
    if noise_band is not None:
        noise_dbm, *_ = _band_power(trace, *noise_band)
        signal_width = abs(signal_band[1] - signal_band[0])
        noise_width = abs(noise_band[1] - noise_band[0])
        if signal_width > 0 and noise_width > 0:
            noise_dbm += 10.0 * np.log10(signal_width / noise_width)
    else:
        estimate = noise_floor(trace, *signal_band)
        frequencies = trace.frequencies_hz
        bin_width = float(np.median(np.abs(np.diff(frequencies)))) if frequencies.size > 1 else 1.0
        bins = max(1.0, abs(signal_band[1] - signal_band[0]) / max(bin_width, 1e-12))
        noise_dbm = estimate.mean_dbm + 10.0 * np.log10(bins)
    signal_mw = float(dbm_to_mw(signal_dbm))
    noise_mw = float(dbm_to_mw(noise_dbm))
    useful_mw = max(signal_mw - noise_mw, np.finfo(float).tiny)
    useful_dbm = float(mw_to_dbm(useful_mw))
    return SnrResult(useful_dbm, noise_dbm, useful_dbm - noise_dbm, signal_band, noise_band)


def harmonic_analysis(
    trace: SpectrumTrace, fundamental_hz: float, harmonic_count: int = 5, search_width_hz: float = 0.0
) -> tuple[list[HarmonicResult], float | None]:
    frequencies, values = _ordered_trace(trace)
    results: list[HarmonicResult] = []
    fundamental_level: float | None = None
    for order in range(1, harmonic_count + 1):
        expected = fundamental_hz * order
        if expected < frequencies[0] or expected > frequencies[-1]:
            continue
        width = search_width_hz or max(abs(trace.frequency_step_hz) * 2.0, 1.0)
        indices = np.flatnonzero(np.abs(frequencies - expected) <= width)
        if not indices.size:
            continue
        index = int(indices[np.argmax(values[indices])])
        level = float(values[index])
        if fundamental_level is None:
            fundamental_level = level
        results.append(HarmonicResult(order, expected, float(frequencies[index]), level, level - fundamental_level))
    if not results or fundamental_level is None:
        return results, None
    harmonic_mw = sum(float(dbm_to_mw(item.level_dbm)) for item in results[1:])
    fundamental_mw = float(dbm_to_mw(fundamental_level))
    thd_percent = 100.0 * np.sqrt(harmonic_mw / fundamental_mw) if fundamental_mw > 0 else None
    return results, thd_percent


def minmax_lod(x: np.ndarray, y: np.ndarray, bucket_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Preserve both extrema of every bucket in their original order."""
    x = np.asarray(x)
    y = np.asarray(y)
    count = min(x.size, y.size)
    if bucket_size <= 1 or count <= 2:
        return x[:count].copy(), y[:count].copy()
    output_indices: list[int] = []
    for start in range(0, count, bucket_size):
        stop = min(count, start + bucket_size)
        block = y[start:stop]
        finite = np.flatnonzero(np.isfinite(block))
        if not finite.size:
            continue
        local_min = int(finite[np.argmin(block[finite])]) + start
        local_max = int(finite[np.argmax(block[finite])]) + start
        output_indices.extend(sorted({local_min, local_max}))
    indices = np.asarray(output_indices, dtype=np.int64)
    return x[indices], y[indices]


def build_lod_pyramid(
    x: np.ndarray, y: np.ndarray, bucket_sizes: tuple[int, ...] = (1, 4, 16, 64, 256)
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    return {size: minmax_lod(x, y, size) for size in bucket_sizes}
