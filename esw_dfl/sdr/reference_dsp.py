"""Explicit float64 golden-reference DSP for future native CPU/CUDA backends.

The implementation favors readable formulas and deterministic numerical
semantics over speed.  It is not used in a high-rate production path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from .contracts import DetectorType, SpectrumUnit, WindowType


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    coefficients: np.ndarray
    coherent_gain: float
    enbw_bins: float
    enbw_hz: float


@dataclass(frozen=True, slots=True)
class ReferenceSpectrum:
    frequencies_hz: np.ndarray
    fft_values: np.ndarray
    power_dbfs_per_bin_linear: np.ndarray
    psd_dbfs_per_hz_linear: np.ndarray
    dbfs_per_bin: np.ndarray
    dbfs_per_hz: np.ndarray
    coherent_gain: float
    enbw_bins: float
    enbw_hz: float
    bin_width_hz: float
    unit_per_bin: SpectrumUnit = SpectrumUnit.DBFS_BIN
    unit_per_hz: SpectrumUnit = SpectrumUnit.DBFS_HZ


def _readonly_1d(value: object, dtype: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=dtype)
    if array.ndim != 1 or array.size == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional array")
    result = np.array(array, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def normalize_adc(
    i_samples: object,
    q_samples: object,
    full_scale: int | float,
    *,
    strict_range: bool = True,
) -> np.ndarray:
    """Normalize integer-like I/Q components from ``[-A, A)`` to complex full scale."""

    scale = float(full_scale)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("full_scale must be finite and positive")
    i_values = np.asarray(i_samples)
    q_values = np.asarray(q_samples)
    if i_values.ndim != 1 or q_values.ndim != 1 or i_values.shape != q_values.shape:
        raise ValueError("I and Q must be equally sized one-dimensional arrays")
    if i_values.size == 0:
        raise ValueError("I/Q arrays must not be empty")
    i_float = np.asarray(i_values, dtype=np.float64)
    q_float = np.asarray(q_values, dtype=np.float64)
    if not np.all(np.isfinite(i_float)) or not np.all(np.isfinite(q_float)):
        raise ValueError("I/Q samples must be finite")
    if strict_range and (
        np.any(i_float < -scale)
        or np.any(i_float >= scale)
        or np.any(q_float < -scale)
        or np.any(q_float >= scale)
    ):
        raise ValueError("ADC samples are outside [-full_scale, full_scale)")
    result = np.asarray(i_float / scale + 1j * (q_float / scale), dtype=np.complex128)
    result.setflags(write=False)
    return result


def window_coefficients(
    window: WindowType,
    size: int,
    *,
    kaiser_beta: float = 8.6,
) -> np.ndarray:
    """Return symmetric analysis-window coefficients using explicit formulas."""

    if not isinstance(window, WindowType):
        raise TypeError("window must be WindowType")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("size must be a positive integer")
    if not math.isfinite(kaiser_beta) or kaiser_beta < 0.0:
        raise ValueError("kaiser_beta must be finite and non-negative")
    if size == 1:
        single_result = np.ones(1, dtype=np.float64)
        single_result.setflags(write=False)
        return single_result

    n = np.arange(size, dtype=np.float64)
    phase = (2.0 * np.pi * n) / float(size - 1)
    values: np.ndarray[Any, np.dtype[np.float64]]
    if window is WindowType.RECTANGULAR:
        values = np.ones(size, dtype=np.float64)
    elif window is WindowType.HANN:
        values = 0.5 - 0.5 * np.cos(phase)
    elif window is WindowType.BLACKMAN_HARRIS_4TERM:
        values = (
            0.35875
            - 0.48829 * np.cos(phase)
            + 0.14128 * np.cos(2.0 * phase)
            - 0.01168 * np.cos(3.0 * phase)
        )
    elif window is WindowType.FLAT_TOP:
        values = (
            0.21557895
            - 0.41663158 * np.cos(phase)
            + 0.277263158 * np.cos(2.0 * phase)
            - 0.083578947 * np.cos(3.0 * phase)
            + 0.006947368 * np.cos(4.0 * phase)
        )
    elif window is WindowType.NUTTALL:
        values = (
            0.355768
            - 0.487396 * np.cos(phase)
            + 0.144232 * np.cos(2.0 * phase)
            - 0.012604 * np.cos(3.0 * phase)
        )
    elif window is WindowType.KAISER:
        alpha = 0.5 * float(size - 1)
        ratio = (n - alpha) / alpha
        values = np.i0(kaiser_beta * np.sqrt(np.maximum(0.0, 1.0 - ratio * ratio))) / np.i0(kaiser_beta)
    else:  # pragma: no cover - exhaustive enum guard
        raise ValueError(f"unsupported window: {window}")
    result = np.asarray(values, dtype=np.float64)
    result.setflags(write=False)
    return result


def coherent_gain(coefficients: object) -> float:
    window = _readonly_1d(coefficients, np.float64, "coefficients")
    gain = float(np.sum(window, dtype=np.float64) / float(window.size))
    if not math.isfinite(gain) or gain <= 0.0:
        raise ValueError("window coherent gain must be positive")
    return gain


def equivalent_noise_bandwidth(
    coefficients: object,
    sample_rate_hz: float,
) -> tuple[float, float]:
    """Return ``(ENBW_bins, ENBW_hz)``."""

    window = _readonly_1d(coefficients, np.float64, "coefficients")
    rate = float(sample_rate_hz)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample_rate_hz must be finite and positive")
    sum_w = float(np.sum(window, dtype=np.float64))
    sum_w2 = float(np.sum(window * window, dtype=np.float64))
    if sum_w <= 0.0 or sum_w2 <= 0.0:
        raise ValueError("window sums must be positive")
    enbw_bins = float(window.size) * sum_w2 / (sum_w * sum_w)
    return enbw_bins, enbw_bins * rate / float(window.size)


def window_metrics(
    window: WindowType,
    size: int,
    sample_rate_hz: float,
    *,
    kaiser_beta: float = 8.6,
) -> WindowMetrics:
    coefficients = window_coefficients(window, size, kaiser_beta=kaiser_beta)
    enbw_bins, enbw_hz = equivalent_noise_bandwidth(coefficients, sample_rate_hz)
    return WindowMetrics(
        coefficients=coefficients,
        coherent_gain=coherent_gain(coefficients),
        enbw_bins=enbw_bins,
        enbw_hz=enbw_hz,
    )


def complex_frequency_axis(
    size: int,
    sample_rate_hz: float,
    *,
    center_frequency_hz: float = 0.0,
) -> np.ndarray:
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("size must be a positive integer")
    rate = float(sample_rate_hz)
    center = float(center_frequency_hz)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample_rate_hz must be finite and positive")
    if not math.isfinite(center):
        raise ValueError("center_frequency_hz must be finite")
    frequencies = center + np.fft.fftshift(np.fft.fftfreq(size, d=1.0 / rate))
    result = np.asarray(frequencies, dtype=np.float64)
    result.setflags(write=False)
    return result


def power_to_db(values_linear: object) -> np.ndarray:
    values = np.asarray(values_linear, dtype=np.float64)
    if np.any(np.isnan(values)) or np.any(values < 0.0):
        raise ValueError("linear power must be non-negative and not NaN")
    result = np.full(values.shape, -np.inf, dtype=np.float64)
    positive = values > 0.0
    result[positive] = 10.0 * np.log10(values[positive])
    result.setflags(write=False)
    return result


def reference_spectrum(
    samples: object,
    sample_rate_hz: float,
    *,
    center_frequency_hz: float = 0.0,
    window: WindowType = WindowType.HANN,
    kaiser_beta: float = 8.6,
) -> ReferenceSpectrum:
    """Compute the explicit complex-baseband FFT, dBFS/bin and dBFS/Hz oracle."""

    iq = _readonly_1d(samples, np.complex128, "samples")
    if not np.all(np.isfinite(iq.real)) or not np.all(np.isfinite(iq.imag)):
        raise ValueError("samples must be finite")
    rate = float(sample_rate_hz)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError("sample_rate_hz must be finite and positive")
    metrics = window_metrics(window, iq.size, rate, kaiser_beta=kaiser_beta)
    weighted = iq * metrics.coefficients
    fft_values = np.fft.fftshift(np.fft.fft(weighted)).astype(np.complex128, copy=False)
    magnitude_squared = np.asarray(fft_values.real**2 + fft_values.imag**2, dtype=np.float64)
    denominator_bin = (float(iq.size) * metrics.coherent_gain) ** 2
    denominator_psd = rate * float(np.sum(metrics.coefficients**2, dtype=np.float64))
    power_per_bin = magnitude_squared / denominator_bin
    psd_per_hz = magnitude_squared / denominator_psd

    arrays = (
        np.asarray(fft_values, dtype=np.complex128),
        np.asarray(power_per_bin, dtype=np.float64),
        np.asarray(psd_per_hz, dtype=np.float64),
    )
    for array in arrays:
        array.setflags(write=False)
    return ReferenceSpectrum(
        frequencies_hz=complex_frequency_axis(
            iq.size,
            rate,
            center_frequency_hz=center_frequency_hz,
        ),
        fft_values=arrays[0],
        power_dbfs_per_bin_linear=arrays[1],
        psd_dbfs_per_hz_linear=arrays[2],
        dbfs_per_bin=power_to_db(arrays[1]),
        dbfs_per_hz=power_to_db(arrays[2]),
        coherent_gain=metrics.coherent_gain,
        enbw_bins=metrics.enbw_bins,
        enbw_hz=metrics.enbw_hz,
        bin_width_hz=rate / float(iq.size),
    )


def parseval_windowed_power(samples: object, coefficients: object) -> float:
    """Return the window-energy-normalized time-domain mean power."""

    iq = _readonly_1d(samples, np.complex128, "samples")
    window = _readonly_1d(coefficients, np.float64, "coefficients")
    if iq.size != window.size:
        raise ValueError("samples and coefficients must have equal length")
    denominator = float(np.sum(window * window, dtype=np.float64))
    return float(np.sum(np.abs(iq * window) ** 2, dtype=np.float64) / denominator)


def integrate_psd(
    psd_per_hz_linear: object,
    bin_width_hz: float,
    *,
    mask: object | None = None,
) -> float:
    psd = _readonly_1d(psd_per_hz_linear, np.float64, "psd_per_hz_linear")
    if np.any(np.isnan(psd)) or np.any(psd < 0.0):
        raise ValueError("PSD must be non-negative and not NaN")
    width = float(bin_width_hz)
    if not math.isfinite(width) or width <= 0.0:
        raise ValueError("bin_width_hz must be finite and positive")
    if mask is not None:
        selected = np.asarray(mask, dtype=bool)
        if selected.shape != psd.shape:
            raise ValueError("mask must match PSD shape")
        psd = psd[selected]
    return float(np.sum(psd, dtype=np.float64) * width)


def integrated_band_power(
    frequencies_hz: object,
    values_linear: object,
    start_hz: float,
    stop_hz: float,
    *,
    density: bool,
    bin_width_hz: float | None = None,
) -> float:
    frequencies = _readonly_1d(frequencies_hz, np.float64, "frequencies_hz")
    values = _readonly_1d(values_linear, np.float64, "values_linear")
    if frequencies.shape != values.shape:
        raise ValueError("frequencies and values must have equal shape")
    start = float(start_hz)
    stop = float(stop_hz)
    if not math.isfinite(start) or not math.isfinite(stop) or stop < start:
        raise ValueError("band limits must be finite and ordered")
    mask = (frequencies >= start) & (frequencies <= stop)
    if not np.any(mask):
        return 0.0
    total = float(np.sum(values[mask], dtype=np.float64))
    if density:
        if bin_width_hz is None:
            if frequencies.size < 2:
                raise ValueError("bin_width_hz is required for a one-bin axis")
            bin_width_hz = float(np.median(np.diff(frequencies)))
        width = float(bin_width_hz)
        if not math.isfinite(width) or width <= 0.0:
            raise ValueError("bin_width_hz must be finite and positive")
        total *= width
    return total


def _stack_linear(frames: Iterable[object]) -> np.ndarray:
    arrays = [np.asarray(frame, dtype=np.float64) for frame in frames]
    if not arrays:
        raise ValueError("at least one frame is required")
    shape = arrays[0].shape
    if any(array.shape != shape for array in arrays):
        raise ValueError("all frames must have equal shape")
    stacked = np.stack(arrays, axis=0)
    if np.any(np.isnan(stacked)) or np.any(stacked < 0.0):
        raise ValueError("linear detector inputs must be non-negative and not NaN")
    return stacked


def linear_average(frames: Iterable[object]) -> np.ndarray:
    result = np.mean(_stack_linear(frames), axis=0, dtype=np.float64)
    result.setflags(write=False)
    return result


def peak_detector(frames: Iterable[object]) -> np.ndarray:
    result = np.max(_stack_linear(frames), axis=0)
    result.setflags(write=False)
    return result


def apply_detector(frames: Iterable[object], detector: DetectorType) -> np.ndarray:
    stacked = _stack_linear(frames)
    if detector is DetectorType.SAMPLE:
        result = stacked[-1]
    elif detector is DetectorType.PEAK:
        result = np.max(stacked, axis=0)
    elif detector is DetectorType.NEGATIVE_PEAK:
        result = np.min(stacked, axis=0)
    elif detector in (DetectorType.RMS, DetectorType.AVERAGE_POWER):
        result = np.mean(stacked, axis=0, dtype=np.float64)
    else:  # pragma: no cover - exhaustive enum guard
        raise ValueError(f"unsupported detector: {detector}")
    output = np.array(result, dtype=np.float64, copy=True)
    output.setflags(write=False)
    return output


__all__ = [
    "ReferenceSpectrum",
    "WindowMetrics",
    "apply_detector",
    "coherent_gain",
    "complex_frequency_axis",
    "equivalent_noise_bandwidth",
    "integrate_psd",
    "integrated_band_power",
    "linear_average",
    "normalize_adc",
    "parseval_windowed_power",
    "peak_detector",
    "power_to_db",
    "reference_spectrum",
    "window_coefficients",
    "window_metrics",
]
