"""Monotonic relative-time axis for a bounded waterfall presentation pane."""

from __future__ import annotations

import pyqtgraph as pg

from ..i18n import text
from .contracts import WaterfallDirection


class WaterfallTimeAxis(pg.AxisItem):
    """Render age labels from row position without treating them as device time."""

    def __init__(self, *, orientation: str = "left") -> None:
        super().__init__(orientation=orientation)
        self._direction = WaterfallDirection.NEWEST_AT_TOP
        self._rows_per_second = 30
        self._display_rows = 0

    def set_presentation_timebase(
        self,
        *,
        direction: WaterfallDirection,
        rows_per_second: int,
        display_rows: int,
    ) -> None:
        self._direction = WaterfallDirection(direction)
        self._rows_per_second = max(1, int(rows_per_second))
        self._display_rows = max(0, int(display_rows))
        self.picture = None
        self.update()

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:
        del scale, spacing
        if self._display_rows == 0:
            return ["" for _ in values]
        return [_format_age_seconds(self._age_seconds(value)) for value in values]

    def _age_seconds(self, row: float) -> float:
        if self._direction is WaterfallDirection.NEWEST_AT_TOP:
            return max(0.0, float(row) / self._rows_per_second)
        return max(0.0, (self._display_rows - float(row)) / self._rows_per_second)


def _format_age_seconds(age_seconds: float) -> str:
    if age_seconds < 1.0:
        return text("waterfall.age.milliseconds", value=age_seconds * 1_000.0)
    if age_seconds < 60.0:
        return text("waterfall.age.seconds", value=age_seconds)
    return text("waterfall.age.minutes", value=age_seconds / 60.0)
