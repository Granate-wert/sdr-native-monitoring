from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np


class SpectrumSmoothMethod(enum.StrEnum):
    """Display interpolation methods for spectrum traces."""

    NONE = "none"
    PCHIP = "pchip"
    MAKIMA = "makima"


class WaterfallSmoothMethod(enum.StrEnum):
    """Display interpolation modes for the waterfall image."""

    NEAREST = "nearest"
    BILINEAR = "bilinear"


@dataclass(slots=True)
class SpectrumSmoothSettings:
    """User-controlled interpolation settings for the spectrum view.

    Interpolation is applied only to the *displayed* curve.  Measurements and
    markers continue to use the raw spectrum trace.
    """

    method: SpectrumSmoothMethod = SpectrumSmoothMethod.NONE
    auto_zoom: bool = True
    zoom_threshold: float = 0.15  # fraction of full X span
    points_per_pixel: float = 2.0


@dataclass(slots=True)
class WaterfallSmoothSettings:
    """User-controlled interpolation settings for the waterfall view."""

    method: WaterfallSmoothMethod = WaterfallSmoothMethod.NEAREST
    auto_zoom: bool = True


def _pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Compute PCHIP (Fritsch-Carlson) derivatives at the knots."""
    n = x.size
    if n < 2:
        return np.zeros_like(y)
    h = np.diff(x)
    delta = np.diff(y) / h
    if n == 2:
        return np.array([delta[0], delta[0]], dtype=y.dtype)
    d = np.empty_like(y)
    d[0] = delta[0]
    d[-1] = delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0.0:
            d[i] = 0.0
        else:
            w1 = 2.0 * h[i] + h[i - 1]
            w2 = h[i] + 2.0 * h[i - 1]
            d[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    return d


def _makima_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Compute modified Akima (Makima) derivatives at the knots."""
    n = x.size
    if n < 2:
        return np.zeros_like(y)
    h = np.diff(x)
    delta = np.diff(y) / h
    if n == 2:
        return np.array([delta[0], delta[0]], dtype=y.dtype)
    # Extrapolate slope differences at the boundaries so that the same formula
    # can be used for every knot.
    left1 = 2.0 * delta[0] - delta[1]
    left2 = 3.0 * delta[0] - 2.0 * delta[1]
    right1 = 2.0 * delta[-1] - delta[-2]
    right2 = 3.0 * delta[-1] - 2.0 * delta[-2]
    delta_ext = np.concatenate(([left2, left1], delta, [right1, right2]))
    d = np.empty_like(y)
    # For knot i use intervals i-2..i+1 of the extended delta array.
    for i in range(n):
        j = i + 2
        dm2, dm1, dp0, dp1 = delta_ext[j - 2], delta_ext[j - 1], delta_ext[j], delta_ext[j + 1]
        w1 = abs(dp1 - dp0) + abs(dp1 + dp0) * 0.5
        w2 = abs(dm1 - dm2) + abs(dm1 + dm2) * 0.5
        if w1 + w2 == 0.0:
            d[i] = (dm1 + dp0) * 0.5
        else:
            d[i] = (w1 * dm1 + w2 * dp0) / (w1 + w2)
    return d


def _hermite_eval(
    x: np.ndarray,
    y: np.ndarray,
    d: np.ndarray,
    xq: np.ndarray,
) -> np.ndarray:
    """Evaluate piecewise cubic Hermite polynomial at query points."""
    n = x.size
    if n == 1:
        return np.full_like(xq, y[0], dtype=np.result_type(y, np.float64))
    bins = np.searchsorted(x, xq, side="right") - 1
    bins = np.clip(bins, 0, n - 2)
    x0 = x[bins]
    x1 = x[bins + 1]
    h = x1 - x0
    t = np.zeros_like(xq)
    finite = h > 0
    t[finite] = (xq[finite] - x0[finite]) / h[finite]
    t = np.clip(t, 0.0, 1.0)
    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2
    y0 = y[bins]
    y1 = y[bins + 1]
    d0 = d[bins]
    d1 = d[bins + 1]
    return h00 * y0 + h10 * h * d0 + h01 * y1 + h11 * h * d1


def interpolate_spectrum(
    x: np.ndarray,
    y: np.ndarray,
    xq: np.ndarray,
    method: SpectrumSmoothMethod,
) -> np.ndarray:
    """Interpolate a spectrum onto a new frequency grid.

    ``x`` must be strictly monotonic (increasing for a normal spectrum).  The
    result preserves the physical unit and keeps the original samples inside
    the convex hull; outside points are clamped to the nearest edge value.
    """
    if method == SpectrumSmoothMethod.NONE:
        return y
    x = np.asarray(x)
    y = np.asarray(y)
    xq = np.asarray(xq)
    if x.size < 2:
        return np.full_like(xq, y[0] if y.size else np.nan, dtype=np.result_type(y, np.float64))
    if method == SpectrumSmoothMethod.PCHIP:
        d = _pchip_slopes(x, y)
    elif method == SpectrumSmoothMethod.MAKIMA:
        d = _makima_slopes(x, y)
    else:
        raise ValueError(f"Unsupported interpolation method: {method}")
    return _hermite_eval(x, y, d, xq)


def upsampled_spectrum_points(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float,
    x_max: float,
    target_points: int,
    method: SpectrumSmoothMethod,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a display-ready (xq, yq) pair for the visible frequency range.

    If the method is ``NONE`` or the target grid is not denser than the
    original one inside the visible window, the original visible samples are
    returned unchanged so that measurements always stay tied to real samples.
    """
    if method == SpectrumSmoothMethod.NONE or target_points <= 1 or x.size < 2:
        mask = (x >= x_min) & (x <= x_max)
        return x[mask].copy(), y[mask].copy()
    xq = np.linspace(float(x_min), float(x_max), target_points)
    yq = interpolate_spectrum(x, y, xq, method)
    return xq, yq
