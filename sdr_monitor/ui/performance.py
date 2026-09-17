"""Bounded UI-thread render telemetry with no native or Qt dependency."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
import time
from typing import Callable


@dataclass(frozen=True, slots=True)
class RenderPerformanceSnapshot:
    """Percentile render telemetry for one coalesced UI surface."""

    schema_version: int = 1
    sample_count: int = 0
    total_samples: int = 0
    render_fps: float = 0.0
    latest_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    budget_ms: float = 0.0
    budget_misses: int = 0


class BoundedRenderMetrics:
    """Keep only recent render samples; never build an unbounded telemetry log."""

    def __init__(self, *, capacity: int = 512) -> None:
        if capacity < 8:
            raise ValueError("render metric capacity must be at least 8")
        self._durations_ms: deque[float] = deque(maxlen=capacity)
        self._timestamps_s: deque[float] = deque(maxlen=capacity)
        self._budget_ms = 0.0
        self._budget_misses = 0
        self._total_samples = 0

    @property
    def capacity(self) -> int:
        return self._durations_ms.maxlen or 0

    def reset(self) -> None:
        """Start a new bounded observation interval without reallocating it.

        UI render metrics are intentionally lifetime-local rather than a
        telemetry log.  A controlled evidence cell must not inherit durations
        or budget misses from its warm-up phase or from an earlier profile.
        """

        self._durations_ms.clear()
        self._timestamps_s.clear()
        self._budget_ms = 0.0
        self._budget_misses = 0
        self._total_samples = 0

    def record(self, duration_ms: float, timestamp_s: float, *, budget_ms: float) -> None:
        duration = float(duration_ms)
        timestamp = float(timestamp_s)
        budget = float(budget_ms)
        if not math.isfinite(duration) or duration < 0.0:
            raise ValueError("render duration must be finite and non-negative")
        if not math.isfinite(timestamp):
            raise ValueError("render timestamp must be finite")
        if not math.isfinite(budget) or budget <= 0.0:
            raise ValueError("render budget must be finite and positive")
        self._durations_ms.append(duration)
        self._timestamps_s.append(timestamp)
        self._total_samples += 1
        self._budget_ms = budget
        if duration > budget:
            self._budget_misses += 1

    def snapshot(self, now_s: float) -> RenderPerformanceSnapshot:
        now = float(now_s)
        if not math.isfinite(now):
            raise ValueError("snapshot timestamp must be finite")
        if not self._durations_ms:
            return RenderPerformanceSnapshot()
        ordered = sorted(self._durations_ms)

        def percentile(quantile: float) -> float:
            index = max(0, math.ceil(quantile * len(ordered)) - 1)
            return ordered[index]

        # A rolling one-second count is easy to read beside native LPS while
        # retaining a strict separation from source/publication rates.
        render_fps = float(sum(timestamp >= now - 1.0 for timestamp in self._timestamps_s))
        return RenderPerformanceSnapshot(
            sample_count=len(ordered),
            total_samples=self._total_samples,
            render_fps=render_fps,
            latest_ms=self._durations_ms[-1],
            p50_ms=percentile(0.50),
            p95_ms=percentile(0.95),
            p99_ms=percentile(0.99),
            budget_ms=self._budget_ms,
            budget_misses=self._budget_misses,
        )


# Keep the earlier S11 API intact. R02 consumes the narrower
# ``BoundedRenderMetrics`` contract in the live workspace rather than changing
# existing callers' measurement or memory-plateau semantics.
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
        p50 = statistics.quantiles(values, n=100, method="inclusive")[49] if len(values) > 1 else values[0]
        p95 = statistics.quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0]
        return PerformanceSummary(len(values), p50, p95, values[-1], self._dropped)

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
        return {
            "samples": len(values),
            "min_bytes": min(values),
            "max_bytes": max(values),
            "growth_bytes": growth,
            "bounded": growth <= max(1, values[0] // 10),
        }


__all__ = [
    "BoundedRenderMetrics",
    "FrameRateMeter",
    "MemoryPlateau",
    "PerformanceSummary",
    "RenderPerformanceSnapshot",
]
