"""Scalar producer-position annotation; no scan simulation or data reduction."""
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from sdr_monitor.domain.sweep_acquisition import SweepSegmentPosition
from ..design import ThemeId, tokens_for_theme
from ..i18n import UiLocale, text


class SweepPositionOverlay:
    """One fixed pair of graphics items in the existing physical ViewBox.

    The interval is the declared usable window of the last admitted segment,
    not its exact finite-bin coverage and not the current RF tuning position.
    It never participates in autorange, markers or analytical statistics.
    """
    def __init__(self, plot: pg.PlotItem, locale: UiLocale) -> None:
        self.position: SweepSegmentPosition | None = None
        self._plot = plot
        self._locale = locale
        self.region = pg.LinearRegionItem(movable=False, span=(0.96, 1.0))
        self.region.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.region.setZValue(12)
        self.label = pg.TextItem(anchor=(0, 0))
        self.label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.label.setZValue(13)
        plot.addItem(self.region, ignoreBounds=True)
        plot.addItem(self.label, ignoreBounds=True)
        plot.getViewBox().sigRangeChanged.connect(self._place_label)
        self.set_theme(ThemeId.DARK)
        self.clear()

    def set_position(self, position: SweepSegmentPosition | None) -> None:
        if position is not None and not isinstance(position, SweepSegmentPosition):
            raise TypeError("Sweep annotation requires a validated producer position")
        if position == self.position:
            return
        self.position = position
        if position is None:
            self.clear()
            return
        self.region.setRegion((position.usable_start_hz, position.usable_stop_hz))
        self.region.show()
        self.set_locale(self._locale)

    def clear(self) -> None:
        self.position = None
        self.region.hide()
        self.label.hide()

    def set_locale(self, locale: UiLocale) -> None:
        self._locale = locale
        if self.position is not None:
            self.label.setText(text("analyzer.position.short", locale, index=self.position.segment_index))
            self.region.setToolTip(text("analyzer.position.scope", locale))
            self._place_label()

    def set_theme(self, theme: ThemeId) -> None:
        colors = tokens_for_theme(theme).colors
        tint = QColor(colors.accent)
        tint.setAlpha(35)
        self.region.setBrush(pg.mkBrush(tint))
        for line in self.region.lines:
            line.setPen(pg.mkPen(colors.accent, width=1, style=Qt.PenStyle.DashLine))
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.label.setColor(colors.primary_text)

    def _place_label(self, *_args) -> None:
        if self.position is None:
            return
        x_range, y_range = self._plot.getViewBox().viewRange()
        lower = max(self.position.usable_start_hz, x_range[0])
        upper = min(self.position.usable_stop_hz, x_range[1])
        self.label.setVisible(lower <= upper)
        self.label.setPos(lower, y_range[1])
