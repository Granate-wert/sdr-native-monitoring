"""Monotonic relative-time axis for a bounded waterfall presentation pane."""

from __future__ import annotations

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
        self._row_origin = (
            0 if self._direction is WaterfallDirection.NEWEST_AT_TOP
            else self._capacity_rows - self._display_rows
        )
        timestamps = np.asarray(timestamps_ns, dtype=np.int64).ravel()
        self._timestamps_ns = (
            timestamps if timestamps_known and timestamps.size == self._display_rows
            else np.empty(0, dtype=np.int64)
        )
        self._gap_rows = _gap_rows(
            self._timestamps_ns,
            direction=self._direction,
            interval_ns=max(1, int(1_000_000_000 // self._rows_per_second)),
        )
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

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
        del scale, spacing
        if self._display_rows == 0 or self._capacity_rows == 0:
            return ["" for _ in values]
        return [self._format_tick(value) for value in values]

    def _format_tick(self, row: float) -> str:
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
                return f"#{stamp.sequence} {status}"
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


def _gap_rows(timestamps_ns: np.ndarray, *, direction: WaterfallDirection, interval_ns: int) -> frozenset[int]:
    if timestamps_ns.size < 2:
        return frozenset()
    newest_indices = np.flatnonzero(np.diff(timestamps_ns) > interval_ns * 2)
    if direction is WaterfallDirection.NEWEST_AT_TOP:
        return frozenset(int(timestamps_ns.size - 1 - index) for index in newest_indices + 1)
    return frozenset(int(index) for index in newest_indices + 1)
