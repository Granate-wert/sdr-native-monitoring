"""Monotonic relative-time axis for a bounded waterfall presentation pane."""

from __future__ import annotations

from math import ceil, floor, isfinite, log10

import pyqtgraph as pg
import numpy as np
from PySide6.QtCore import QRectF

from ..i18n import UiLocale, text
from .contracts import WaterfallDirection
from .sweep_rows import SweepRowStamp, SweepRowState


class WaterfallTimeAxis(pg.AxisItem):
    """Render age labels from row position without treating them as device time."""

    def __init__(self, *, orientation: str = "left", locale: UiLocale = UiLocale.RU) -> None:
        super().__init__(orientation=orientation)
        self._locale = locale
        self._direction = WaterfallDirection.NEWEST_AT_TOP
        self._rows_per_second = 30
        self._display_rows = 0
        self._capacity_rows = 0
        self._row_origin = 0
        self._timestamps_ns: np.ndarray = np.empty(0, dtype=np.int64)
        self._gap_rows: frozenset[int] = frozenset()
        self._sweep_stamps: tuple[SweepRowStamp | None, ...] = ()
        self._has_sweep_stamps = False
        self._multiple_sweep_epochs = False
        self._age_ticks: dict[float, int] = {}
        self._age_step_ns: int | None = None
        self._producer_interval_ns = 1_000_000_000 // self._rows_per_second
        # Numerical labels must not repeatedly shrink/expand the plot gutter.
        # A genuinely wider value/font may still expand it once.
        self.setStyle(autoReduceTextSpace=False)

    def set_locale(self, locale: UiLocale) -> None:
        self._locale = UiLocale(locale)
        self.picture = None
        self.update()

    def set_presentation_timebase(
        self,
        *,
        direction: WaterfallDirection,
        rows_per_second: int,
        display_rows: int,
        capacity_rows: int,
        timestamps_ns: np.ndarray,
        timestamps_known: bool,
        sweep_stamps: tuple[SweepRowStamp | None, ...] = (),
    ) -> None:
        self._direction = WaterfallDirection(direction)
        self._rows_per_second = max(1, int(rows_per_second))
        self._display_rows = max(0, int(display_rows))
        self._capacity_rows = max(self._display_rows, int(capacity_rows))
        self._sweep_stamps = sweep_stamps
        self._has_sweep_stamps = (
            len(sweep_stamps) == self._display_rows
            and any(stamp is not None for stamp in sweep_stamps)
        )
        self._multiple_sweep_epochs = len({stamp.acquisition_epoch for stamp in sweep_stamps
                                           if stamp is not None}) > 1
        self._row_origin = (
            0 if self._direction is WaterfallDirection.NEWEST_AT_TOP
            else self._capacity_rows - self._display_rows
        )
        timestamps = np.asarray(timestamps_ns, dtype=np.int64).ravel()
        self._timestamps_ns = (
            timestamps if timestamps_known and timestamps.size == self._display_rows
            else np.empty(0, dtype=np.int64)
        )
        self._producer_interval_ns = _typical_interval_ns(
            self._timestamps_ns, max(1, int(1_000_000_000 // self._rows_per_second)),
        )
        self._gap_rows = _gap_rows(
            self._timestamps_ns,
            direction=self._direction,
            interval_ns=self._producer_interval_ns,
        )
        self._age_ticks.clear()
        if not self._timestamps_ns.size:
            self._age_step_ns = None
        self.picture = None
        self.update()

    def generateDrawSpecs(self, painter):
        """Cull only overlapping Sweep labels after Qt resolves font and DPI.

        The newest label offered by pyqtgraph keeps priority. All row stamps
        and raw tick strings remain available; this limits only the text
        actually painted on a crowded axis, independently of tick levels.
        """
        specs = super().generateDrawSpecs(painter)
        if specs is None or not self._has_sweep_stamps or self.orientation not in ("left", "right"):
            return specs
        axis_spec, tick_specs, text_specs = specs
        newest_at_top = self._direction is WaterfallDirection.NEWEST_AT_TOP
        ordered = sorted(text_specs, key=lambda item: item[0].center().y(), reverse=not newest_at_top)
        retained = []
        previous: QRectF | None = None
        for spec in ordered:
            rect, _, label = spec
            if not label:
                continue
            padded = rect.adjusted(0.0, -2.0, 0.0, 2.0)
            # Every left/right-axis label shares an aligned x edge. Sorted by
            # y, the last accepted rectangle is the only possible collision.
            if previous is not None and padded.intersects(previous):
                continue
            retained.append(spec)
            previous = padded
        return axis_spec, tick_specs, retained

    def tickValues(self, minVal: float, maxVal: float, size: float):
        """Offer the actual latest Sweep row at the highest text level.

        Automatic nice-number ticks can omit the last row, especially when
        new rows are anchored at the bottom. Promotion changes only the axis
        proposal; overlap culling still decides what can be painted.
        """
        self._age_ticks.clear()
        if (not self._has_sweep_stamps and self._timestamps_ns.size
                and self.orientation in ("left", "right") and not self.logMode):
            return self._relative_time_ticks(minVal, maxVal, size)
        levels = super().tickValues(minVal, maxVal, size)
        if (not self._has_sweep_stamps or not self._display_rows
                or self._sweep_stamps[-1] is None
                or self.orientation not in ("left", "right") or self.logMode):
            return levels
        latest = float(
            self._row_origin if self._direction is WaterfallDirection.NEWEST_AT_TOP
            else self._row_origin + self._display_rows - 1
        )
        if not min(minVal, maxVal) <= latest <= max(minVal, maxVal):
            return levels
        if not levels:
            return [(1.0, [latest])]
        spacing, major = levels[0]
        if latest in major:
            return levels
        promoted = [(spacing, sorted([*major, latest]))]
        promoted.extend((step, [value for value in values if value != latest])
                        for step, values in levels[1:])
        return promoted

    def _relative_time_ticks(self, minimum: float, maximum: float, size: float):
        """Stable age text, placed using source time rather than fixed rows.

        Interpolate only between adjacent observed rows without a producer
        pause. A tick inside a missing-time interval is omitted, not invented.
        No wall-clock timer advances this axis after Stop/freeze.
        """
        if not all(isfinite(value) for value in (minimum, maximum, size)) or size <= 0:
            return []
        timestamps = self._timestamps_ns
        newest = int(timestamps[-1])
        span_ns = newest - int(timestamps[0])
        if span_ns < 0 or np.any(np.diff(timestamps) < 0):
            return []
        # Reserve the configured history span even while the ring fills. This
        # avoids rescaling the tick ladder on every incoming row. Longer real
        # history may expand the scale; hysteresis avoids a boundary flip-flop.
        row_span = min(float(self._capacity_rows), abs(maximum - minimum))
        extent_ns = max(span_ns, int(row_span * 1_000_000_000 / self._rows_per_second), 1)
        tick_intervals = max(1, min(19, int(size // 52)))
        target_ns = extent_ns / tick_intervals
        step_ns = self._age_step_ns
        if step_ns is None or target_ns > step_ns * 1.05 or target_ns < step_ns * 0.4:
            step_ns = _nice_age_step_ns(target_ns)
            self._age_step_ns = step_ns
        minimum, maximum = min(minimum, maximum), max(minimum, maximum)
        values: list[float] = []
        max_gap_ns = self._producer_interval_ns * 2
        for age_ns in range(0, span_ns + 1, step_ns):
            source_row = _source_row_at_time(timestamps, newest - age_ns, max_gap_ns)
            if source_row is None:
                continue
            row = self._row_origin + (
                self._display_rows - 1 - source_row
                if self._direction is WaterfallDirection.NEWEST_AT_TOP else source_row
            )
            if minimum <= row <= maximum:
                values.append(row)
                self._age_ticks[row] = age_ns
        # Spacing is a row-coordinate hint for pyqtgraph, not a time estimate.
        return [(max(1.0, abs(maximum - minimum) / tick_intervals), sorted(values))]

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
        del scale, spacing
        if self._display_rows == 0 or self._capacity_rows == 0:
            return ["" for _ in values]
        return [self._format_tick(value) for value in values]

    def _format_tick(self, row: float) -> str:
        if row in self._age_ticks:
            label = _format_age_seconds(self._age_ticks[row] / 1_000_000_000, self._locale)
            index = int(round(row)) - self._row_origin
            # Mark only an exact observed gap boundary, not an interpolated row.
            return f"{label} ⏸" if row == round(row) and index in self._gap_rows else label
        index = int(round(row)) - self._row_origin
        if not 0 <= index < self._display_rows:
            return ""
        source_index = (
            self._display_rows - 1 - index
            if self._direction is WaterfallDirection.NEWEST_AT_TOP else index
        )
        if len(self._sweep_stamps) == self._display_rows:
            stamp = self._sweep_stamps[source_index]
            if stamp is not None:
                # Axis fonts may lack checkmark/ellipsis glyphs. ASCII markers
                # remain readable under Windows fallback and are explained by
                # the localized tooltip/accessibility description.
                status = {SweepRowState.PARTIAL: "P", SweepRowState.COMPLETE: "C", SweepRowState.GAP: "G"}[stamp.state]
                epoch = (f"E{stamp.acquisition_epoch} "
                         if self._multiple_sweep_epochs and stamp.acquisition_epoch is not None else "")
                return f"{epoch}#{stamp.sequence} {status}"
            if self._has_sweep_stamps:
                return text("waterfall.sweep.visit_gap", self._locale)
        if self._timestamps_ns.size:
            age_seconds = max(0.0, (int(self._timestamps_ns[-1]) - int(self._timestamps_ns[source_index])) / 1_000_000_000)
            label = _format_age_seconds(age_seconds, self._locale)
            # A compact glyph marks a producer-time pause without claiming a
            # device clock domain or replacing missing rows with fake samples.
            return f"{label} ⏸" if index in self._gap_rows else label
        # Unknown provenance is sequence-only.  Do not convert render cadence
        # or a raw unqualified scalar into a precise RF/producer age.
        return ""

    def _age_seconds(self, row: float) -> float:
        if self._direction is WaterfallDirection.NEWEST_AT_TOP:
            return max(0.0, float(row) / self._rows_per_second)
        return max(0.0, (self._display_rows - float(row)) / self._rows_per_second)


def _format_age_seconds(age_seconds: float, locale: UiLocale = UiLocale.RU) -> str:
    if age_seconds < 1.0:
        return text("waterfall.age.milliseconds", locale, value=age_seconds * 1_000.0)
    if age_seconds < 60.0:
        return text("waterfall.age.seconds", locale, value=age_seconds)
    return text("waterfall.age.minutes", locale, value=age_seconds / 60.0)


def _nice_age_step_ns(target_ns: float) -> int:
    """1/2/5 time ladder, with millisecond minimum and integral ns storage."""
    target_ns = max(1_000_000.0, target_ns)
    power = 10 ** floor(log10(target_ns))
    fraction = target_ns / power
    factor = 1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10
    return max(1_000_000, int(ceil(factor * power)))


def _source_row_at_time(timestamps: np.ndarray, target_ns: int, max_gap_ns: int) -> float | None:
    """Map a source-time anchor to observed rows; do not fill temporal gaps."""
    index = int(np.searchsorted(timestamps, target_ns, side="left"))
    if index < timestamps.size and int(timestamps[index]) == target_ns:
        return float(index)
    if index == 0 or index == timestamps.size:
        return None
    before, after = int(timestamps[index - 1]), int(timestamps[index])
    interval = after - before
    if interval <= 0 or interval > max_gap_ns:
        return None
    return index - 1 + (target_ns - before) / interval


def _typical_interval_ns(timestamps: np.ndarray, configured_ns: int) -> int:
    """Lower median observed row step, not a device acquisition duty cycle.

    A presentation cap is not a promise that the producer supplies that many
    rows. The lower median also keeps a single long pause from setting the
    baseline in a two-interval history.
    """
    intervals = np.diff(timestamps)
    positive = intervals[intervals > 0]
    if not positive.size:
        return configured_ns
    middle = (positive.size - 1) // 2
    return max(configured_ns, int(np.partition(positive, middle)[middle]))


def _gap_rows(timestamps_ns: np.ndarray, *, direction: WaterfallDirection, interval_ns: int) -> frozenset[int]:
    if timestamps_ns.size < 2:
        return frozenset()
    newest_indices = np.flatnonzero(np.diff(timestamps_ns) > interval_ns * 2)
    if direction is WaterfallDirection.NEWEST_AT_TOP:
        return frozenset(int(timestamps_ns.size - 1 - index) for index in newest_indices + 1)
    return frozenset(int(index) for index in newest_indices + 1)
