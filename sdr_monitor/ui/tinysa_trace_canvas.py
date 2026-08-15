"""Small paint-only canvas for bounded tinySA presentation extrema."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..services.tinysa_trace_presentation import TinySaTracePresentation


@dataclass(frozen=True, slots=True)
class TinySaTraceCanvasMetrics:
    """Scalar-only paint telemetry; trace arrays never enter the metrics path."""

    paint_events: int
    trace_paint_events: int
    empty_paint_events: int
    painted_points_total: int
    last_paint_duration_ns: int
    maximum_paint_duration_ns: int


class TinySaTraceCanvas(QWidget):
    """Render only reduced points; the full 10,001-point trace stays outside Qt."""

    trace_painted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presentation: TinySaTracePresentation | None = None
        self._paint_events = 0
        self._trace_paint_events = 0
        self._empty_paint_events = 0
        self._painted_points_total = 0
        self._last_paint_duration_ns = 0
        self._maximum_paint_duration_ns = 0
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("tinySA spectrum trace")
        self.setAccessibleDescription("No tinySA trace has been acquired")

    @property
    def presentation(self) -> TinySaTracePresentation | None:
        return self._presentation

    @property
    def metrics(self) -> TinySaTraceCanvasMetrics:
        return TinySaTraceCanvasMetrics(
            paint_events=self._paint_events,
            trace_paint_events=self._trace_paint_events,
            empty_paint_events=self._empty_paint_events,
            painted_points_total=self._painted_points_total,
            last_paint_duration_ns=self._last_paint_duration_ns,
            maximum_paint_duration_ns=self._maximum_paint_duration_ns,
        )

    def set_presentation(self, presentation: TinySaTracePresentation) -> None:
        if not isinstance(presentation, TinySaTracePresentation):
            raise TypeError("tinySA canvas requires bounded presentation data")
        self._presentation = presentation
        self.setAccessibleDescription(
            f"{presentation.source_point_count} analytical points rendered as "
            f"{presentation.display_point_count} peak-preserving extrema"
        )
        self.update()

    def clear(self) -> None:
        self._presentation = None
        self.setAccessibleDescription("No tinySA trace has been acquired")
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        started_ns = time.perf_counter_ns()
        painted_points = 0
        trace_painted = False
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.fillRect(self.rect(), self.palette().base())
            bounds = QRectF(self.rect()).adjusted(12.0, 12.0, -12.0, -12.0)
            presentation = self._presentation
            if presentation is None or presentation.display_point_count == 0:
                painter.setPen(self.palette().text().color())
                painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "No trace")
                return

            frequencies = presentation.frequencies_hz
            values = presentation.values_dbm
            frequency_min = float(frequencies[0])
            frequency_span = max(1.0, float(frequencies[-1]) - frequency_min)
            value_min = float(values.min())
            value_max = float(values.max())
            value_span = max(1.0, value_max - value_min)

            def point(index: int) -> QPointF:
                x = bounds.left() + (
                    (float(frequencies[index]) - frequency_min) / frequency_span
                ) * bounds.width()
                y = bounds.bottom() - (
                    (float(values[index]) - value_min) / value_span
                ) * bounds.height()
                return QPointF(x, y)

            path = QPainterPath(point(0))
            for index in range(1, presentation.display_point_count):
                path.lineTo(point(index))
            pen = QPen(self.palette().highlight().color())
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.drawPath(path)
            painted_points = presentation.display_point_count
            trace_painted = True
        finally:
            if painter.isActive():
                painter.end()
            elapsed_ns = max(0, time.perf_counter_ns() - started_ns)
            self._paint_events += 1
            self._trace_paint_events += int(trace_painted)
            self._empty_paint_events += int(not trace_painted)
            self._painted_points_total += painted_points
            self._last_paint_duration_ns = elapsed_ns
            self._maximum_paint_duration_ns = max(
                self._maximum_paint_duration_ns,
                elapsed_ns,
            )
            if trace_painted:
                self.trace_painted.emit(self.metrics)


__all__ = ["TinySaTraceCanvas", "TinySaTraceCanvasMetrics"]
