from __future__ import annotations

import numpy as np


def power_average_db(values_db: np.ndarray, axis: int | None = None) -> np.ndarray:
    """Average logarithmic power values in the linear domain."""
    values = np.asarray(values_db, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        linear = np.power(10.0, values / 10.0)
        return 10.0 * np.log10(np.nanmean(linear, axis=axis))


def integrated_power_db(values_db: np.ndarray) -> float:
    """Sum logarithmic power samples in the linear domain."""
    values = np.asarray(values_db, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        return float(10.0 * np.log10(np.nansum(np.power(10.0, values / 10.0))))


def occupied_bandwidth(
    frequencies_hz: np.ndarray,
    values_db: np.ndarray,
    fraction: float = 0.99,
) -> tuple[float, float, float]:
    """Return low, high and width containing the requested power fraction."""
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    values = np.asarray(values_db, dtype=np.float64)
    count = min(frequencies.size, values.size)
    if count == 0 or not 0.0 < fraction <= 1.0:
        return np.nan, np.nan, np.nan
    frequencies = frequencies[:count]
    values = values[:count]
    finite = np.isfinite(frequencies) & np.isfinite(values)
    if not finite.any():
        return np.nan, np.nan, np.nan
    frequencies = frequencies[finite]
    with np.errstate(over="ignore", invalid="ignore"):
        power = np.power(10.0, values[finite] / 10.0)
    total = float(np.sum(power))
    if not np.isfinite(total) or total <= 0.0:
        return np.nan, np.nan, np.nan
    cumulative = np.cumsum(power) / total
    tail = (1.0 - fraction) / 2.0
    low_index = min(int(np.searchsorted(cumulative, tail, side="left")), frequencies.size - 1)
    high_index = min(
        int(np.searchsorted(cumulative, 1.0 - tail, side="left")),
        frequencies.size - 1,
    )
    low = float(frequencies[low_index])
    high = float(frequencies[high_index])
    return low, high, high - low


def local_peak_indices(values_db: np.ndarray) -> np.ndarray:
    """Find strict interior peaks, keeping finite edge maxima as candidates."""
    values = np.asarray(values_db, dtype=np.float64)
    if values.size == 0:
        return np.empty(0, dtype=np.int64)
    candidates: list[int] = []
    if values.size == 1:
        return np.array([0], dtype=np.int64) if np.isfinite(values[0]) else np.empty(0, dtype=np.int64)
    if np.isfinite(values[0]) and values[0] > values[1]:
        candidates.append(0)
    middle = np.flatnonzero(
        np.isfinite(values[1:-1])
        & (values[1:-1] > values[:-2])
        & (values[1:-1] >= values[2:])
    ) + 1
    candidates.extend(int(middle[index]) for index in range(middle.size))
    if np.isfinite(values[-1]) and values[-1] > values[-2]:
        candidates.append(values.size - 1)
    return np.asarray(candidates, dtype=np.int64)
