"""Axis primitives owned exclusively by the V2 measurement scene."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QLineF

from ..i18n import UiLocale
from .contracts import format_frequency_hz


class BatchedAxisItem(pg.AxisItem):
    """Record adjacent equal-pen ticks as one QPicture drawLines command.

    Uses the existing AxisItem picture, not an additional bitmap/cache. Keep
    order across pen changes and the original independent-segment semantics.
    No tick omission, aggregation, geometry rounding or rendering-hint change.
    """

    def drawPicture(self, painter, axisSpec, tickSpecs, textSpecs) -> None:  # noqa: N802
        painter.setRenderHint(painter.RenderHint.Antialiasing, False)
        painter.setRenderHint(painter.RenderHint.TextAntialiasing, True)
        pen, first, last = axisSpec
        painter.setPen(pen)
        painter.drawLine(first, last)
        lines: list[QLineF] = []
        current_pen = None
        for pen, first, last in tickSpecs:
            if lines and pen != current_pen:
                painter.drawLines(lines)
                lines.clear()
            if not lines:
                painter.setPen(pen)
                current_pen = pen
            lines.append(QLineF(first, last))
        if lines:
            painter.drawLines(lines)
        if self.style["tickFont"] is not None:
            painter.setFont(self.style["tickFont"])
        painter.setPen(self.textPen())
        painter.setClipRect(self.boundingRect().toAlignedRect())
        for rect, flags, value in textSpecs:
            painter.drawText(rect, int(flags), value)


class FrequencyAxis(BatchedAxisItem):
    """Frequency axis that never guesses a display unit from signal amplitude."""

    def __init__(self, *args, locale: UiLocale = UiLocale.RU, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._locale = locale

    def set_locale(self, locale: UiLocale) -> None:
        self._locale = UiLocale(locale)
        self.picture = None
        self.update()

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:  # noqa: N802 - Qt API.
        del scale
        return [format_frequency_hz(value, locale=self._locale, resolution_hz=spacing) for value in values]
