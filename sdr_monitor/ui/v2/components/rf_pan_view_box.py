"""Middle drag proposes an RF offset; it never pans data or controls a device."""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGraphicsItem


class RfPanViewBox(pg.ViewBox):
    """Same gesture for Spectrum/Waterfall, independent of RTBW/Sweep DSP.

    The owner supplies a small immutable measurement/control anchor. Sequence,
    progressive revision and array identity are deliberately not prescribed
    here. No provider means no RF gesture, NOT a fallback middle viewport pan.
    Rightward grab shifts the requested RF window down in frequency.
    """

    rf_shift_requested = Signal(float, object)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self._rf_provider: Callable[[], object | None] | None = None
        self._rf_drag: tuple[object, float, float] | None = None
        self.sigXRangeChanged.connect(self._cancel_on_range_change)

    def _cancel_on_range_change(self, _view: object, _range: object) -> None:
        if self._rf_drag is not None:
            self.cancel_rf_drag()

    def set_rf_shift_provider(self, provider: Callable[[], object | None] | None) -> None:
        if provider is not None and not callable(provider):
            raise TypeError("RF gesture needs a cached measurement anchor provider")
        self.cancel_rf_drag()
        self._rf_provider = provider

    def cancel_rf_drag(self) -> None:
        self._rf_drag = None
        self.unsetCursor()

    def _anchor(self) -> object | None:
        try:
            return None if self._rf_provider is None else self._rf_provider()
        except Exception:
            return None  # No stale UI data may authorize a receiver command.

    def mouseDragEvent(self, ev, axis=None) -> None:
        if ev.button() != Qt.MouseButton.MiddleButton:
            super().mouseDragEvent(ev, axis=axis)
            return
        ev.accept()
        if ev.isStart():
            self.cancel_rf_drag()
            anchor = self._anchor()
            lower, upper = self.viewRange()[0]
            width = float(self.width())
            if (anchor is None or not all(isfinite(value) for value in (lower, upper, width))
                    or width <= 0 or upper <= lower):
                return
            self._rf_drag = anchor, float(ev.buttonDownPos().x()), (upper - lower) / width
            self.setFocus()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        captured = self._rf_drag
        if captured is None:
            return
        try:
            unchanged = bool(self._anchor() == captured[0])
        except (TypeError, ValueError):
            unchanged = False
        if not unchanged:
            self.cancel_rf_drag()
            return
        if ev.isFinish():
            delta_pixels = float(ev.pos().x()) - captured[1]
            shift_hz = -delta_pixels * captured[2]
            self.cancel_rf_drag()
            if isfinite(shift_hz) and abs(delta_pixels) >= 3.0:
                self.rf_shift_requested.emit(shift_hz, captured[0])

    def wheelEvent(self, ev, axis=None) -> None:
        self.cancel_rf_drag()  # A zoom during a held drag cancels that RF proposal.
        super().wheelEvent(ev, axis=axis)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape and self._rf_drag is not None:
            self.cancel_rf_drag()
            ev.accept()
            return
        super().keyPressEvent(ev)


__all__ = ["RfPanViewBox"]
