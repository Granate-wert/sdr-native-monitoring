"""Monotonic relative-time axis for a bounded waterfall presentation pane."""

from __future__ import annotations

import pyqtgraph as pg
import numpy as np

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
