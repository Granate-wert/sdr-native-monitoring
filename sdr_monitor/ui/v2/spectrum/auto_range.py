"""Bounded, clipping-safe Auto Y presentation policy.

The full-resolution finite extent is supplied by the existing spectrum path;
this helper owns only two display bounds and one monotonic dwell timestamp.
"""

from __future__ import annotations

_SHRINK_DWELL_NS = 1_500_000_000
_SHRINK_SPAN_RATIO = 0.70


class AutoVerticalRange:
    """Hold small noise-floor changes; react immediately to new extrema."""

    def __init__(self) -> None:
        self.bounds: tuple[float, float] | None = None
        self._shrink_since_ns: int | None = None

    def reset(self) -> None:
        self.bounds = None
        self._shrink_since_ns = None

    def update(self, extent: tuple[float, float] | None, *, now_ns: int) -> tuple[float, float] | None:
        """Return display bounds without ever omitting a supplied finite extremum.

        A disjoint new range is an immediate retune. Otherwise, outward moves
        are immediate and inward moves require a sustained, materially smaller
        candidate. Nonfinite-only frames retain the last accepted bounds.
        """

        if extent is None:
            self._shrink_since_ns = None
            return self.bounds

        minimum, maximum = extent
        data_span = maximum - minimum
        margin = max(3.0, data_span * 0.08)
        total_span = max(20.0, data_span + 2.0 * margin)
        padding = (total_span - data_span) / 2.0
        candidate = (minimum - padding, maximum + padding)

        current = self.bounds
        if current is None or candidate[1] < current[0] or candidate[0] > current[1]:
            self.bounds = candidate
            self._shrink_since_ns = None
            return candidate

        if candidate[0] < current[0] or candidate[1] > current[1]:
            expanded = (min(current[0], candidate[0]), max(current[1], candidate[1]))
            self.bounds = expanded
            self._shrink_since_ns = None
            return expanded

        candidate_span = candidate[1] - candidate[0]
        current_span = current[1] - current[0]
        if candidate_span <= current_span * _SHRINK_SPAN_RATIO:
            if self._shrink_since_ns is None or now_ns < self._shrink_since_ns:
                self._shrink_since_ns = now_ns
            elif now_ns - self._shrink_since_ns >= _SHRINK_DWELL_NS:
                self.bounds = candidate
                self._shrink_since_ns = None
                return candidate
        else:
            self._shrink_since_ns = None
        return current
