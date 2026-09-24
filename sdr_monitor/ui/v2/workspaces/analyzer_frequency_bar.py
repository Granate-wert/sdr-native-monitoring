"""One compact frequency bar: RF draft editors and explicitly local viewport."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QWidget

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewState
from .analyzer_configuration import AnalyzerConfigurationDrawer


class AnalyzerFrequencyBar(QWidget):
    """The same RF editor widgets remain backed by one configuration draft."""

    viewport_span_requested = Signal(float)

    def __init__(self, drawer: AnalyzerConfigurationDrawer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drawer = drawer
        self._bindings: list[tuple[QLabel | QPushButton, str]] = []
        self._rtbw: list[QWidget] = []
        self._sweep: list[QWidget] = []
        self._mode = AnalyzerMode.RTBW
        self._has_frame = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.center, self.fft, self.gain = drawer.take_quick_controls()
        self.center.setMaximumWidth(116)
        self.fft.setMaximumWidth(88)
        self.gain.setMaximumWidth(82)
        self.span = self._frequency(100.0)
        self.span.setDecimals(6)
        self.span.setRange(0.000001, 100_000.0)
        self.span.setSpecialValueText("—")
        self.span.setMaximumWidth(112)
        self.span.valueChanged.connect(lambda value: self.viewport_span_requested.emit(value * 1e6))
        self.start = self._frequency(100.0)
        self.stop = self._frequency(1000.0)
        self._field(row, "analyzer.quick.center", self.center, self._rtbw)
        self._field(row, "analyzer.quick.span", self.span, self._rtbw)
        self._field(row, "analyzer.start_frequency", self.start, self._sweep)
        self._field(row, "analyzer.stop_frequency", self.stop, self._sweep)
        self._field(row, "analyzer.quick.fft", self.fft)
        self._field(row, "analyzer.quick.gain", self.gain)
        row.addStretch(1)
        self.apply = self._button(row, "analyzer.apply", drawer.apply_draft)
        self.cancel = self._button(row, "analyzer.cancel", drawer.cancel_draft)
        self.set_locale()

    def _frequency(self, value: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox(self)
        field.setProperty("ui2Role", "range-control")
        field.setDecimals(3)
        field.setRange(0.001, 100_000.0)
        field.setMaximumWidth(116)
        field.setValue(value)
        return field

    def _field(self, row, key: str, field: QWidget, mode_widgets: list[QWidget] | None = None) -> None:
        label = QLabel(self)
        label.setProperty("ui2Role", "secondary")
        label.setBuddy(field)
        self._bindings.append((label, key))
        row.addWidget(label)
        row.addWidget(field)
        if mode_widgets is not None:
            mode_widgets.extend((label, field))

    def _button(self, row, key, callback) -> QPushButton:
        button = QPushButton(self)
        button.setProperty("ui2Role", "utility-action")
        button.clicked.connect(callback)
        self._bindings.append((button, key))
        row.addWidget(button)
        return button

    def set_locale(self) -> None:
        self.setAccessibleName(text("analyzer.quick.name"))
        for widget, key in self._bindings:
            widget.setText(text(key))
            widget.setAccessibleName(text(key))
            if isinstance(widget, QLabel) and widget.buddy() is not None:
                widget.buddy().setAccessibleName(text(key))
        self.span.setToolTip(text("analyzer.quick.span_hint"))
        for field in (self.center, self.fft, self.gain):
            field.setToolTip(text("analyzer.quick.rf_hint"))

    def apply_view_state(self, state: AnalyzerViewState, *, has_frame: bool) -> None:
        self._mode, self._has_frame = state.mode, has_frame
        for widget in self._rtbw:
            widget.setVisible(state.mode is AnalyzerMode.RTBW)
        for widget in self._sweep:
            widget.setVisible(state.mode is AnalyzerMode.SWEEP)
        self.start.setEnabled(not state.controls_locked)
        self.stop.setEnabled(not state.controls_locked)
        # RF editor enabled/dirty state is owned by the drawer. Viewport zoom
        # remains available during RX and never changes that draft.
        self.span.setEnabled(has_frame)
        if not has_frame:
            # A mode/source change can leave the previous plot's X range in
            # ViewBox until the next frame. Never present it as this mode's
            # current viewport while the scene is empty.
            with QSignalBlocker(self.span):
                self.span.setValue(self.span.minimum())
        self.apply.setEnabled(self._drawer.can_apply)
        self.cancel.setEnabled(self._drawer.can_cancel)

    def set_viewport_span(self, span_hz: float) -> None:
        if not self._has_frame:
            return
        with QSignalBlocker(self.span):
            self.span.setValue(span_hz / 1e6)
