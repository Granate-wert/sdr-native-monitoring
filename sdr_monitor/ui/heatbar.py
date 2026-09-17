"""Interactive scientific colour-level control for standalone SDR views.

The widget owns presentation state only.  It exposes an ordered low/high
window over fixed bounds and emits explicit changes for Spectrum, Waterfall or
Persistence.  Both horizontal and vertical variants are DPI-aware; the
vertical variant mirrors the compact heatbar used by measurement instruments.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget


_PALETTES: dict[str, tuple[tuple[float, str], ...]] = {
    "viridis": ((0.0, "#440154"), (0.25, "#3B528B"), (0.5, "#21918C"), (0.75, "#5EC962"), (1.0, "#FDE725")),
    "inferno": ((0.0, "#000004"), (0.25, "#420A68"), (0.5, "#932667"), (0.75, "#DD513A"), (1.0, "#FCFFA4")),
    "turbo": ((0.0, "#30123B"), (0.2, "#4662D7"), (0.4, "#35B779"), (0.6, "#FDE725"), (0.8, "#F98E09"), (1.0, "#7A0403")),
    "grayscale": ((0.0, "#050505"), (0.5, "#777777"), (1.0, "#F5F5F5")),
    "spectrum": ((0.0, "#1B2633"), (0.5, "#4DA3FF"), (1.0, "#55D6BE")),
}


@dataclass(frozen=True, slots=True)
class HeatBarLevels:
    minimum: float
    maximum: float


class InteractiveHeatBar(QWidget):
    """Two-handle gradient level selector suitable for high-DPI SDR UIs."""

    levels_changed = Signal(float, float)
    levels_committed = Signal(float, float)

    _HANDLE_RADIUS = 8.0
    _BAR_THICKNESS = 14.0

    def __init__(
        self,
        *,
        bounds: tuple[float, float] = (-140.0, 20.0),
        levels: tuple[float, float] = (-115.0, -55.0),
        palette: str = "viridis",
        unit: str = "dB",
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._bound_min = float(bounds[0])
        self._bound_max = float(bounds[1])
        self._level_min = float(levels[0])
        self._level_max = float(levels[1])
        self._palette = palette if palette in _PALETTES else "viridis"
        self._unit = unit
        self._orientation = orientation
        self._drag_target: str | None = None
        self._drag_start_position = 0.0
        self._drag_start_levels = (self._level_min, self._level_max)
        if self._orientation is Qt.Orientation.Vertical:
            self.setMinimumSize(74, 160)
            self.setMaximumWidth(92)
        else:
            self.setMinimumSize(180, 48)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Interactive colour level range")
        self.setToolTip(
            "Перетащите маркеры для границ; перетащите выделенный диапазон для сдвига; "
            "колесо изменяет ширину диапазона; двойной щелчок сбрасывает уровни."
        )
        self.set_bounds(*bounds)
        self.set_levels(*levels, emit=False)

    def sizeHint(self) -> QSize:
        if self._orientation is Qt.Orientation.Vertical:
            return QSize(82, 260)
        return QSize(320, 48)

    @property
    def levels(self) -> HeatBarLevels:
        return HeatBarLevels(self._level_min, self._level_max)

    @property
    def bounds(self) -> HeatBarLevels:
        return HeatBarLevels(self._bound_min, self._bound_max)

    @property
    def orientation(self) -> Qt.Orientation:
        return self._orientation

    def set_palette(self, palette: str) -> None:
        self._palette = palette if palette in _PALETTES else "viridis"
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = str(unit)
        self.update()

    def set_bounds(self, minimum: float, maximum: float) -> None:
        lo, hi = sorted((float(minimum), float(maximum)))
        if not (lo == lo and hi == hi):
            return
        if hi - lo <= 1e-12:
            hi = lo + 1.0
        self._bound_min, self._bound_max = lo, hi
        self.set_levels(self._level_min, self._level_max, emit=False)

    def set_levels(self, minimum: float, maximum: float, *, emit: bool = False) -> None:
        lo, hi = sorted((float(minimum), float(maximum)))
        if not (lo == lo and hi == hi):
            return
        lo = max(self._bound_min, min(lo, self._bound_max))
        hi = max(self._bound_min, min(hi, self._bound_max))
        minimum_width = max(1e-9, (self._bound_max - self._bound_min) / 1000.0)
        if hi - lo < minimum_width:
            if hi + minimum_width <= self._bound_max:
                hi = lo + minimum_width
            else:
                lo = hi - minimum_width
        changed = abs(lo - self._level_min) > 1e-12 or abs(hi - self._level_max) > 1e-12
        self._level_min, self._level_max = lo, hi
        if changed:
            self.update()
            if emit:
                self.levels_changed.emit(lo, hi)

    def reset_to_bounds(self) -> None:
        self.set_levels(self._bound_min, self._bound_max, emit=True)
        self.levels_committed.emit(self._level_min, self._level_max)

    def _bar_rect(self) -> QRectF:
        margin = self._HANDLE_RADIUS + 5.0
        if self._orientation is Qt.Orientation.Vertical:
            top = margin
            height = max(1.0, self.height() - 2.0 * margin)
            return QRectF(9.0, top, self._BAR_THICKNESS, height)
        width = max(1.0, self.width() - 2.0 * margin)
        top = max(18.0, (self.height() - self._BAR_THICKNESS) * 0.5)
        return QRectF(margin, top, width, self._BAR_THICKNESS)

    def _value_to_position(self, value: float) -> float:
        bar = self._bar_rect()
        ratio = (float(value) - self._bound_min) / (self._bound_max - self._bound_min)
        ratio = max(0.0, min(1.0, ratio))
        if self._orientation is Qt.Orientation.Vertical:
            return bar.bottom() - ratio * bar.height()
        return bar.left() + ratio * bar.width()

    def _position_to_value(self, position: float) -> float:
        bar = self._bar_rect()
        if self._orientation is Qt.Orientation.Vertical:
            ratio = (bar.bottom() - float(position)) / max(1.0, bar.height())
        else:
            ratio = (float(position) - bar.left()) / max(1.0, bar.width())
        return self._bound_min + max(0.0, min(1.0, ratio)) * (self._bound_max - self._bound_min)

    def _event_position(self, event: QMouseEvent | QWheelEvent) -> float:
        return event.position().y() if self._orientation is Qt.Orientation.Vertical else event.position().x()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bar = self._bar_rect()

        if self._orientation is Qt.Orientation.Vertical:
            gradient = QLinearGradient(bar.bottomLeft(), bar.topLeft())
        else:
            gradient = QLinearGradient(bar.topLeft(), bar.topRight())
        for position, colour in _PALETTES[self._palette]:
            gradient.setColorAt(position, QColor(colour))
        painter.setPen(QPen(QColor("#44515E"), 1.0))
        painter.setBrush(gradient)
        painter.drawRoundedRect(bar, 3.0, 3.0)

        low_pos = self._value_to_position(self._level_min)
        high_pos = self._value_to_position(self._level_max)
        shade = QColor(8, 12, 17, 165)
        if self._orientation is Qt.Orientation.Vertical:
            painter.fillRect(QRectF(bar.left(), bar.top(), bar.width(), max(0.0, high_pos - bar.top())), shade)
            painter.fillRect(QRectF(bar.left(), low_pos, bar.width(), max(0.0, bar.bottom() - low_pos)), shade)
            handles = ((low_pos, self._drag_target == "low"), (high_pos, self._drag_target == "high"))
            for y, selected in handles:
                painter.setPen(QPen(QColor("#E8EEF5"), 2.0))
                painter.setBrush(QColor("#3CA6FF") if selected else QColor("#9EABB8"))
                painter.drawEllipse(QPointF(bar.center().x(), y), self._HANDLE_RADIUS, self._HANDLE_RADIUS)
            painter.setPen(QColor("#E8EEF5"))
            label_left = bar.right() + 8.0
            label_width = max(10.0, self.width() - label_left)
            painter.drawText(
                QRectF(label_left, bar.top() - 2.0, label_width, 34.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                self._format_value(self._level_max),
            )
            painter.drawText(
                QRectF(label_left, bar.bottom() - 32.0, label_width, 34.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                self._format_value(self._level_min),
            )
        else:
            painter.fillRect(QRectF(bar.left(), bar.top(), max(0.0, low_pos - bar.left()), bar.height()), shade)
            painter.fillRect(QRectF(high_pos, bar.top(), max(0.0, bar.right() - high_pos), bar.height()), shade)
            handles = ((low_pos, self._drag_target == "low"), (high_pos, self._drag_target == "high"))
            for x, selected in handles:
                painter.setPen(QPen(QColor("#E8EEF5"), 2.0))
                painter.setBrush(QColor("#3CA6FF") if selected else QColor("#9EABB8"))
                painter.drawEllipse(QPointF(x, bar.center().y()), self._HANDLE_RADIUS, self._HANDLE_RADIUS)
            painter.setPen(QColor("#E8EEF5"))
            painter.drawText(
                QRectF(bar.left(), 0.0, bar.width() * 0.5, 17.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._format_value(self._level_min),
            )
            painter.drawText(
                QRectF(bar.center().x(), 0.0, bar.width() * 0.5, 17.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._format_value(self._level_max),
            )

    def _format_value(self, value: float) -> str:
        if abs(value) >= 1000:
            text = f"{value:.0f}"
        elif abs(value) >= 100:
            text = f"{value:.1f}"
        else:
            text = f"{value:.2f}"
        return f"{text} {self._unit}".strip()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is not Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        position = self._event_position(event)
        low_pos = self._value_to_position(self._level_min)
        high_pos = self._value_to_position(self._level_max)
        if abs(position - low_pos) <= self._HANDLE_RADIUS * 1.6:
            target = "low"
        elif abs(position - high_pos) <= self._HANDLE_RADIUS * 1.6:
            target = "high"
        elif min(low_pos, high_pos) < position < max(low_pos, high_pos):
            target = "window"
        else:
            target = "low" if abs(position - low_pos) < abs(position - high_pos) else "high"
        self._drag_target = target
        self._drag_start_position = position
        self._drag_start_levels = (self._level_min, self._level_max)
        self.grabMouse()
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_target is None:
            return super().mouseMoveEvent(event)
        value = self._position_to_value(self._event_position(event))
        start_low, start_high = self._drag_start_levels
        if self._drag_target == "low":
            self.set_levels(value, self._level_max, emit=True)
        elif self._drag_target == "high":
            self.set_levels(self._level_min, value, emit=True)
        else:
            start_value = self._position_to_value(self._drag_start_position)
            delta = value - start_value
            width = start_high - start_low
            low = start_low + delta
            high = start_high + delta
            if low < self._bound_min:
                low, high = self._bound_min, self._bound_min + width
            if high > self._bound_max:
                high, low = self._bound_max, self._bound_max - width
            self.set_levels(low, high, emit=True)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_target is not None:
            self._drag_target = None
            self.releaseMouse()
            self.update()
            self.levels_committed.emit(self._level_min, self._level_max)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self.reset_to_bounds()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            return super().wheelEvent(event)
        center = self._position_to_value(self._event_position(event))
        width = self._level_max - self._level_min
        factor = 0.88**steps
        full_width = self._bound_max - self._bound_min
        new_width = max(full_width / 1000.0, min(full_width, width * factor))
        ratio = (center - self._level_min) / max(width, 1e-12)
        low = center - new_width * ratio
        high = low + new_width
        if low < self._bound_min:
            low, high = self._bound_min, self._bound_min + new_width
        if high > self._bound_max:
            high, low = self._bound_max, self._bound_max - new_width
        self.set_levels(low, high, emit=True)
        self.levels_committed.emit(self._level_min, self._level_max)
        event.accept()


__all__ = ["HeatBarLevels", "InteractiveHeatBar"]
