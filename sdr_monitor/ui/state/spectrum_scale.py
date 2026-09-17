"""Application-owned scale state and smooth autoscale controllers.

PyQtGraph is a renderer only.  Manual/automatic policy belongs to the
standalone SDR presentation layer, so a new SpectrumFrame can never silently
overwrite a user-selected range.

Continuous autoscale uses a sliding window of compact robust envelopes.  It
expands immediately when the current frame would be clipped, holds the wider
range after a transient, and contracts slowly with hysteresis.  Ordinary noise
therefore does not make the axes jump on every frame.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
import math
from typing import Deque

import numpy as np


class AxisScaleMode(Enum):
    MANUAL = auto()
    AUTO_ONCE = auto()
    AUTO_CONTINUOUS = auto()
    FOLLOW_CENTER_SPAN = auto()


@dataclass(frozen=True, slots=True)
class SpectrumScaleState:
    """Immutable scale policy for one presentation.

    ``x_min_hz``/``x_max_hz`` are always real limits, never center/span values.
    ``reference_level_db`` is the top of a conventional analyser graticule.
    """

    x_mode: AxisScaleMode = AxisScaleMode.FOLLOW_CENTER_SPAN
    y_mode: AxisScaleMode = AxisScaleMode.AUTO_ONCE
    x_min_hz: float = 2.372e9
    x_max_hz: float = 2.428e9
    y_min_db: float = -120.0
    y_max_db: float = -20.0
    reference_level_db: float = -20.0
    scale_per_div_db: float = 10.0
    x_locked: bool = False
    y_locked: bool = False

    def with_y(
        self,
        y_min_db: float,
        y_max_db: float,
        *,
        mode: AxisScaleMode | None = None,
        divisions: int = 10,
    ) -> "SpectrumScaleState":
        if not math.isfinite(y_min_db) or not math.isfinite(y_max_db) or y_max_db <= y_min_db:
            raise ValueError("Y scale requires finite ordered limits")
        return SpectrumScaleState(
            x_mode=self.x_mode,
            y_mode=self.y_mode if mode is None else mode,
            x_min_hz=self.x_min_hz,
            x_max_hz=self.x_max_hz,
            y_min_db=float(y_min_db),
            y_max_db=float(y_max_db),
            reference_level_db=float(y_max_db),
            scale_per_div_db=float((y_max_db - y_min_db) / max(1, divisions)),
            x_locked=self.x_locked,
            y_locked=self.y_locked,
        )

    def with_x(
        self,
        x_min_hz: float,
        x_max_hz: float,
        *,
        mode: AxisScaleMode | None = None,
    ) -> "SpectrumScaleState":
        if not math.isfinite(x_min_hz) or not math.isfinite(x_max_hz) or x_max_hz <= x_min_hz:
            raise ValueError("X scale requires finite ordered limits")
        return SpectrumScaleState(
            x_mode=self.x_mode if mode is None else mode,
            y_mode=self.y_mode,
            x_min_hz=float(x_min_hz),
            x_max_hz=float(x_max_hz),
            y_min_db=self.y_min_db,
            y_max_db=self.y_max_db,
            reference_level_db=self.reference_level_db,
            scale_per_div_db=self.scale_per_div_db,
            x_locked=self.x_locked,
            y_locked=self.y_locked,
        )

    def with_y_mode(self, mode: AxisScaleMode) -> "SpectrumScaleState":
        return SpectrumScaleState(
            x_mode=self.x_mode,
            y_mode=mode,
            x_min_hz=self.x_min_hz,
            x_max_hz=self.x_max_hz,
            y_min_db=self.y_min_db,
            y_max_db=self.y_max_db,
            reference_level_db=self.reference_level_db,
            scale_per_div_db=self.scale_per_div_db,
            x_locked=self.x_locked,
            y_locked=self.y_locked,
        )

    def with_y_lock(self, locked: bool) -> "SpectrumScaleState":
        return SpectrumScaleState(
            x_mode=self.x_mode,
            y_mode=self.y_mode,
            x_min_hz=self.x_min_hz,
            x_max_hz=self.x_max_hz,
            y_min_db=self.y_min_db,
            y_max_db=self.y_max_db,
            reference_level_db=self.reference_level_db,
            scale_per_div_db=self.scale_per_div_db,
            x_locked=self.x_locked,
            y_locked=bool(locked),
        )

    def with_reference_divisions(
        self,
        reference_level_db: float,
        scale_per_div_db: float,
        *,
        divisions: int = 10,
    ) -> "SpectrumScaleState":
        if not math.isfinite(reference_level_db) or not math.isfinite(scale_per_div_db):
            raise ValueError("reference/division values must be finite")
        if scale_per_div_db <= 0.0 or divisions < 1:
            raise ValueError("scale per division and division count must be positive")
        y_max = float(reference_level_db)
        y_min = y_max - float(scale_per_div_db) * divisions
        return SpectrumScaleState(
            x_mode=self.x_mode,
            y_mode=AxisScaleMode.MANUAL,
            x_min_hz=self.x_min_hz,
            x_max_hz=self.x_max_hz,
            y_min_db=y_min,
            y_max_db=y_max,
            reference_level_db=y_max,
            scale_per_div_db=float(scale_per_div_db),
            x_locked=self.x_locked,
            y_locked=self.y_locked,
        )


@dataclass(frozen=True, slots=True)
class FrameScaleEnvelope:
    timestamp_s: float
    robust_low_db: float
    robust_high_db: float
    hard_min_db: float
    hard_max_db: float


@dataclass(frozen=True, slots=True)
class AutoscaleConfig:
    window_s: float = 2.0
    low_percentile: float = 1.0
    high_percentile: float = 99.5
    window_high_percentile: float = 90.0
    bottom_margin_db: float = 5.0
    top_margin_db: float = 8.0
    emergency_headroom_db: float = 2.0
    emergency_bottom_headroom_db: float = 1.0
    emergency_top_margin_db: float = 6.0
    emergency_bottom_margin_db: float = 3.0
    hold_time_s: float = 1.5
    release_time_s: float = 4.0
    hysteresis_db: float = 2.0
    min_range_db: float = 40.0
    max_range_db: float = 200.0
    auto_once_grid_step_db: float = 5.0
    # Backward-compatible alias used by earlier tests/configuration files.
    # Continuous autoscale remains unrounded; the value is used by auto-once.
    grid_step_db: float = 0.0
    range_update_rate_hz: float = 10.0
    max_window_samples: int = 1200

    def __post_init__(self) -> None:
        if self.window_s <= 0.0 or self.release_time_s <= 0.0 or self.hold_time_s < 0.0:
            raise ValueError("window/release must be positive; hold non-negative")
        if not 0.0 < self.low_percentile < self.high_percentile < 100.0:
            raise ValueError("percentiles must satisfy 0 < low < high < 100")
        if not 0.0 < self.window_high_percentile <= 100.0:
            raise ValueError("window high percentile must be in (0, 100]")
        if self.min_range_db <= 0.0 or self.max_range_db < self.min_range_db:
            raise ValueError("range bounds must be positive and ordered")
        if self.range_update_rate_hz <= 0.0:
            raise ValueError("range update rate must be positive")
        if self.max_window_samples < 2:
            raise ValueError("autoscale window sample bound must be at least 2")


def calculate_envelope(
    values_db: np.ndarray,
    timestamp_s: float,
    *,
    low_percentile: float = 1.0,
    high_percentile: float = 99.5,
) -> FrameScaleEnvelope:
    """Return robust and hard limits for one spectrum frame."""
    finite = np.atleast_1d(np.asarray(values_db, dtype=np.float64))
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return FrameScaleEnvelope(timestamp_s, -140.0, -60.0, -140.0, -60.0)
    low = float(np.percentile(finite, low_percentile))
    high = float(np.percentile(finite, high_percentile))
    return FrameScaleEnvelope(timestamp_s, low, high, float(finite.min()), float(finite.max()))


def _median(values: Deque[float]) -> float:
    if not values:
        return 0.0
    return float(np.median(np.fromiter(values, dtype=np.float64)))


def _percentile(values: Deque[float], percentile: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.fromiter(values, dtype=np.float64), percentile))


class SmoothAutoscaleController:
    """Sliding-window autoscale with fast expansion and slow contraction."""

    def __init__(self, config: AutoscaleConfig | None = None) -> None:
        self._config = config or AutoscaleConfig()
        self._window: Deque[FrameScaleEnvelope] = deque()
        self._current_min_db = -120.0
        self._current_max_db = -20.0
        self._upper_hold_until_s = 0.0
        self._lower_hold_until_s = 0.0
        self._last_frame_s: float | None = None
        self._last_range_update_s: float | None = None

    @property
    def config(self) -> AutoscaleConfig:
        return self._config

    @property
    def current_range(self) -> tuple[float, float]:
        return (self._current_min_db, self._current_max_db)

    def reset(self, *, min_db: float = -120.0, max_db: float = -20.0) -> None:
        if max_db <= min_db:
            raise ValueError("autoscale reset requires ordered bounds")
        self._window.clear()
        self._current_min_db = float(min_db)
        self._current_max_db = float(max_db)
        self._upper_hold_until_s = 0.0
        self._lower_hold_until_s = 0.0
        self._last_frame_s = None
        self._last_range_update_s = None

    def calculate_once(self, values_db: np.ndarray) -> tuple[float, float]:
        """Calculate a robust range immediately, without attack/release state."""
        envelope = calculate_envelope(
            values_db,
            0.0,
            low_percentile=self._config.low_percentile,
            high_percentile=self._config.high_percentile,
        )
        y_min = min(
            envelope.robust_low_db - self._config.bottom_margin_db,
            envelope.hard_min_db - self._config.emergency_bottom_margin_db,
        )
        y_max = max(
            envelope.robust_high_db + self._config.top_margin_db,
            envelope.hard_max_db + self._config.emergency_top_margin_db,
        )
        grid_step = self._config.grid_step_db or self._config.auto_once_grid_step_db
        y_min, y_max = self._normalize_range(
            y_min,
            y_max,
            grid_step_db=grid_step,
            required_min_db=envelope.hard_min_db,
            required_max_db=envelope.hard_max_db,
        )
        self.reset(min_db=y_min, max_db=y_max)
        return (y_min, y_max)

    def update(self, values_db: np.ndarray, timestamp_s: float) -> tuple[float, float]:
        envelope = calculate_envelope(
            values_db,
            timestamp_s,
            low_percentile=self._config.low_percentile,
            high_percentile=self._config.high_percentile,
        )
        self._window.append(envelope)
        cutoff = timestamp_s - self._config.window_s
        while self._window and self._window[0].timestamp_s < cutoff:
            self._window.popleft()
        while len(self._window) > self._config.max_window_samples:
            self._window.popleft()

        low_values: Deque[float] = deque(item.robust_low_db for item in self._window)
        high_values: Deque[float] = deque(item.robust_high_db for item in self._window)
        target_low = _median(low_values) - self._config.bottom_margin_db
        target_high = _percentile(high_values, self._config.window_high_percentile) + self._config.top_margin_db

        frame_dt_s = 0.0 if self._last_frame_s is None else max(0.0, timestamp_s - self._last_frame_s)
        self._last_frame_s = timestamp_s

        emergency_high = envelope.hard_max_db > self._current_max_db - self._config.emergency_headroom_db
        emergency_low = envelope.hard_min_db < self._current_min_db + self._config.emergency_bottom_headroom_db

        # Emergency expansion is never rate-limited: the current frame must fit.
        if emergency_high:
            self._current_max_db = max(
                self._current_max_db,
                envelope.hard_max_db + self._config.emergency_top_margin_db,
            )
            self._upper_hold_until_s = timestamp_s + self._config.hold_time_s
        if emergency_low:
            self._current_min_db = min(
                self._current_min_db,
                envelope.hard_min_db - self._config.emergency_bottom_margin_db,
            )
            self._lower_hold_until_s = timestamp_s + self._config.hold_time_s

        minimum_update_period = 1.0 / self._config.range_update_rate_hz
        range_update_due = (
            self._last_range_update_s is None
            or timestamp_s - self._last_range_update_s >= minimum_update_period
        )
        if range_update_due:
            range_dt_s = (
                frame_dt_s
                if self._last_range_update_s is None
                else max(0.0, timestamp_s - self._last_range_update_s)
            )
            self._last_range_update_s = timestamp_s
            if not emergency_high and timestamp_s >= self._upper_hold_until_s:
                self._current_max_db = self._release_toward(self._current_max_db, target_high, range_dt_s)
            if not emergency_low and timestamp_s >= self._lower_hold_until_s:
                self._current_min_db = self._release_toward(self._current_min_db, target_low, range_dt_s)

        self._current_min_db, self._current_max_db = self._normalize_range(
            self._current_min_db,
            self._current_max_db,
            grid_step_db=0.0,
            required_min_db=envelope.hard_min_db,
            required_max_db=envelope.hard_max_db,
        )
        return self.current_range

    def _release_toward(self, current: float, target: float, dt_s: float) -> float:
        if abs(target - current) < self._config.hysteresis_db or dt_s <= 0.0:
            return current
        alpha = 1.0 - math.exp(-dt_s / self._config.release_time_s)
        return current + alpha * (target - current)

    def _normalize_range(
        self,
        y_min: float,
        y_max: float,
        *,
        grid_step_db: float,
        required_min_db: float | None = None,
        required_max_db: float | None = None,
    ) -> tuple[float, float]:
        if not math.isfinite(y_min) or not math.isfinite(y_max):
            return self.current_range
        if y_max <= y_min:
            return self.current_range

        required_min = y_min if required_min_db is None or not math.isfinite(required_min_db) else float(required_min_db)
        required_max = y_max if required_max_db is None or not math.isfinite(required_max_db) else float(required_max_db)
        if required_max < required_min:
            required_min, required_max = required_max, required_min

        span = y_max - y_min
        if span < self._config.min_range_db:
            center = (y_max + y_min) / 2.0
            half = self._config.min_range_db / 2.0
            y_min, y_max = center - half, center + half

        def clamp_max_span(lo: float, hi: float) -> tuple[float, float]:
            span_now = hi - lo
            required_span = required_max - required_min
            # A hard signal range wider than the configured limit wins: an
            # autoscale must never clip the current spectrum merely to obey a
            # cosmetic maximum span.
            if span_now <= self._config.max_range_db or required_span > self._config.max_range_db:
                return lo, hi
            center = (hi + lo) / 2.0
            half = self._config.max_range_db / 2.0
            candidate_lo, candidate_hi = center - half, center + half
            if required_max > candidate_hi:
                shift = required_max - candidate_hi
                candidate_lo += shift
                candidate_hi += shift
            if required_min < candidate_lo:
                shift = candidate_lo - required_min
                candidate_lo -= shift
                candidate_hi -= shift
            return candidate_lo, candidate_hi

        y_min, y_max = clamp_max_span(y_min, y_max)
        if grid_step_db > 0.0:
            y_min = math.floor(y_min / grid_step_db) * grid_step_db
            y_max = math.ceil(y_max / grid_step_db) * grid_step_db
            y_min, y_max = clamp_max_span(y_min, y_max)

        # Last-resort inclusion guarantee for the actual current frame.
        y_min = min(y_min, required_min)
        y_max = max(y_max, required_max)
        return (float(y_min), float(y_max))



__all__ = [
    "AxisScaleMode",
    "SpectrumScaleState",
    "FrameScaleEnvelope",
    "AutoscaleConfig",
    "SmoothAutoscaleController",
    "calculate_envelope",
]
