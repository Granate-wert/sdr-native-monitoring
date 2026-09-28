"""Host-observed rate of a native cumulative completed-Sweep-line counter.

This is neither the RF revisit interval nor the producer's exact completion
timestamp. A zero-delta UI poll must not erase a recent observation, while a
stalled producer must not leave an apparently current rate indefinitely.
"""

from __future__ import annotations

import math


class CompletedLineRateObservation:
    """Bounded-lifetime LPS estimate, reset for each explicitly started owner."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._count: int | None = None
        self._last_poll_s: float | None = None
        self._last_change_s: float | None = None
        self._rate_hz = 0.0

    def observe(self, completed_lines: int, now_s: float) -> float:
        if type(completed_lines) is not int or completed_lines < 0:
            raise ValueError("completed Sweep line count must be a nonnegative integer")
        if not math.isfinite(now_s):
            raise ValueError("completed Sweep line observation time must be finite")
        if self._last_poll_s is not None and now_s < self._last_poll_s:
            raise ValueError("completed Sweep line observation time regressed")
        self._last_poll_s = now_s
        previous = self._count
        if previous is None:
            self._count = completed_lines
            self._last_change_s = now_s
            return 0.0
        if completed_lines < previous:
            raise ValueError("completed Sweep line count regressed within one owner")
        if completed_lines > previous:
            assert self._last_change_s is not None
            elapsed = now_s - self._last_change_s
            if elapsed > 0.0:
                rate = (completed_lines - previous) / elapsed
                if not math.isfinite(rate):
                    raise ValueError("completed Sweep line rate is not finite")
                self._rate_hz = rate
            self._count = completed_lines
            self._last_change_s = now_s
        elif self._rate_hz > 0.0:
            assert self._last_change_s is not None
            stale_after_s = min(120.0, max(1.0, 3.0 / self._rate_hz))
            if now_s - self._last_change_s >= stale_after_s:
                self._rate_hz = 0.0
        return self._rate_hz


__all__ = ["CompletedLineRateObservation"]
