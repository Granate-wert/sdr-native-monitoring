"""Three reusable graphics items; bounded coverage and a separate history trace."""
from collections.abc import Callable
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from ..design import ThemeId, tokens_for_theme
from ..i18n import UiLocale, text
from .sweep_coverage import CURRENT, MISSING, PREVIOUS, CoverageProjection, SweepCoverageState


class _CoverageStrip(pg.GraphicsObject):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.rect = QRectF()
        self.runs: list[tuple[float, float, int]] = []
        self.theme = ThemeId.DARK

    def boundingRect(self) -> QRectF:
        return self.rect

    def set_rect(self, rect: QRectF) -> None:
        if rect != self.rect:
            self.prepareGeometryChange()
            self.rect = rect
            self.update()

    def set_projection(self, projection: CoverageProjection) -> None:
        states, edges = projection.states, projection.edges_hz
        cuts = np.r_[0, np.flatnonzero(states[1:] != states[:-1]) + 1, states.size]
        self.runs = [(float(edges[a]), float(edges[b]), int(states[a]))
                     for a, b in zip(cuts[:-1], cuts[1:]) if b > a]
        self.update()

    def paint(self, painter: QPainter, *_args) -> None:
        colors = tokens_for_theme(self.theme).colors
        painter.save()
        painter.setClipRect(self.rect)
        painter.setPen(Qt.PenStyle.NoPen)
        # Position annotation owns the top 4%; coverage uses the next strip.
        bottom = self.rect.top()
        top = self.rect.bottom()
        height = self.rect.height()
        for left, right, state in self.runs:
            color = QColor(colors.success if state == CURRENT else
                           colors.secondary_text if state == PREVIOUS else colors.warning)
            style = (Qt.BrushStyle.SolidPattern if state == CURRENT else
                     Qt.BrushStyle.HorPattern if state == PREVIOUS else
                     Qt.BrushStyle.BDiagPattern if state == MISSING else Qt.BrushStyle.DiagCrossPattern)
            painter.setBrush(QBrush(color, style))
            painter.drawRect(QRectF(left, top - height * .085, right - left, height * .035))
            if state & MISSING:
                # At subpixel resolution this is deliberately conservative:
                # any absent bin marks the column, not fabricated continuity.
                color.setAlpha(45 if self.theme is not ThemeId.HIGH_CONTRAST else 100)
                painter.setBrush(QBrush(color, Qt.BrushStyle.BDiagPattern))
                painter.drawRect(QRectF(left, bottom, right - left, height * .90))
        painter.restore()


class SweepCoverageOverlay:
    """No device commands, clock, accumulation or independent publication queue."""
    def __init__(self, plot: pg.PlotItem, locale: UiLocale) -> None:
        self.state = SweepCoverageState()
        self._presentation_active = True
        self.projection: CoverageProjection | None = None
        self._plot = plot
        self._locale = locale
        self._projection_key: tuple[object, ...] | None = None
        self.request_projection: Callable[[], None] | None = None
        self._display_previous = None
        self.strip = _CoverageStrip()
        self.strip.setZValue(-5)
        self.history = pg.PlotDataItem(connect="finite")
        self.history.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.history.setZValue(-1)
        self.label = pg.TextItem(anchor=(0, 1))
        self.label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.label.setZValue(11)
        for item in (self.strip, self.history, self.label):
            plot.addItem(item, ignoreBounds=True)
        plot.getViewBox().sigRangeChanged.connect(self.refresh)
        plot.getViewBox().sigResized.connect(self.refresh)
        self.set_theme(ThemeId.DARK)
        self.clear()

    def accept(self, snapshot: ContinuousSweepDisplaySnapshot) -> None:
        if self.state.accept(snapshot):
            if self.state.current is None:
                self.clear()
                return
            self._projection_key = None
            self.refresh()
            if self.request_projection is None:
                self.set_locale(self._locale)

    def clear(self) -> None:
        self.state.clear()
        self.projection = None
        self._display_previous = None
        self._projection_key = None
        self.strip.runs = []
        self.history.clear()
        for item in (self.strip, self.history, self.label):
            item.hide()

    def set_presentation_active(self, active: bool) -> None:
        self._presentation_active = bool(active)
        if self._presentation_active:
            self.refresh()

    def refresh(self, *_args) -> None:
        if not self._presentation_active:
            return
        if self.state.current is None:
            for item in (self.strip, self.history, self.label):
                item.hide()
            return
        view = self._plot.getViewBox()
        (left, right), (lower, upper) = view.viewRange()
        width = max(1, int(view.width()))
        key = (left, right, width)
        if key != self._projection_key:
            if self.request_projection is not None:
                self.request_projection()
            else:
                self.apply_projection(self.state.project(left, right, width), key, self.state.previous)
        self.strip.set_rect(QRectF(left, lower, right - left, upper - lower))
        self.label.setPos(left, lower)
        self.strip.setVisible(self.projection is not None)
        self.label.setVisible(self.projection is not None)
        self.history.setVisible(self.projection is not None and self._display_previous is not None)

    def apply_projection(self, projection: CoverageProjection, key: tuple[object, ...], previous) -> None:
        """GUI upload of a bounded projection with its exact historical label."""
        self.projection = projection
        self._projection_key = key
        self._display_previous = previous
        self.strip.set_projection(projection)
        trace = projection.history
        self.history.setData(trace.frequencies_hz, trace.values, connect="finite")
        self.set_locale(self._locale)

    def set_locale(self, locale: UiLocale) -> None:
        self._locale = locale
        self.label.setText(text("analyzer.coverage.legend", locale))
        previous = self._display_previous if self.request_projection is not None else self.state.previous
        detail = text("analyzer.coverage.scope", locale)
        if previous is not None:
            detail += " " + text("analyzer.coverage.previous", locale, sequence=previous.sequence)
        for item in (self.strip, self.history, self.label):
            item.setToolTip(detail)

    def set_theme(self, theme: ThemeId) -> None:
        colors = tokens_for_theme(theme).colors
        self.strip.theme = theme
        self.strip.update()
        self.history.setPen(pg.mkPen(colors.secondary_text, width=1.2, style=Qt.PenStyle.DashLine))
        self.label.setColor(colors.secondary_text)
