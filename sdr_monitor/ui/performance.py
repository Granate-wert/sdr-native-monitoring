"""Bounded, testable UI performance measurements for S11."""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    samples: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    dropped_publications: int = 0

    @property
    def meets_60hz(self) -> bool:
        return self.p95_ms <= 16.67


class FrameRateMeter:
    def __init__(self, capacity: int = 4096) -> None:
        if capacity <= 0:
            raise ValueError("performance sample capacity must be positive")
        self._samples: deque[float] = deque(maxlen=capacity)
        self._dropped = 0

    def record(self, duration_ms: float) -> None:
        if duration_ms < 0:
            raise ValueError("duration must not be negative")
        if len(self._samples) == self._samples.maxlen:
            self._dropped += 1
        self._samples.append(float(duration_ms))

    def measure(self, callback: Callable[[], object]) -> object:
        started = time.perf_counter()
        result = callback()
        self.record((time.perf_counter() - started) * 1000.0)
        return result

    def summary(self) -> PerformanceSummary:
        values = sorted(self._samples)
        if not values:
            return PerformanceSummary(0, 0.0, 0.0, 0.0, self._dropped)
        return PerformanceSummary(len(values), statistics.quantiles(values, n=100, method="inclusive")[49] if len(values) > 1 else values[0], statistics.quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0], values[-1], self._dropped)

    def samples(self) -> tuple[float, ...]:
        return tuple(self._samples)


class MemoryPlateau:
    """Bounded memory proxy: keep only recent observations for soak assertions."""

    def __init__(self, capacity: int = 120) -> None:
        self._values: deque[int] = deque(maxlen=capacity)

    def record(self, value_bytes: int) -> None:
        if value_bytes < 0:
            raise ValueError("memory value must not be negative")
        self._values.append(value_bytes)

    def summary(self) -> dict[str, int | float | bool]:
        values = tuple(self._values)
        if not values:
            return {"samples": 0, "min_bytes": 0, "max_bytes": 0, "growth_bytes": 0, "bounded": True}
        growth = values[-1] - values[0]
        return {"samples": len(values), "min_bytes": min(values), "max_bytes": max(values), "growth_bytes": growth, "bounded": growth <= max(1, values[0] // 10)}


__all__ = ["FrameRateMeter", "MemoryPlateau", "PerformanceSummary"]
