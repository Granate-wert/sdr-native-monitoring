"""Compact overlay hosting the existing analyzer presentation controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ..i18n import UiLocale, text


class AnalyzerDisplayControls(QFrame):
    """Presentation-only owner of detached Spectrum and Waterfall toolbars."""

    close_requested = Signal()
    viewport_range_requested = Signal(float, float)

    def __init__(self, spectrum_controls: QWidget, waterfall_controls: QWidget,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("ui2Role", "popover")
        self.setMaximumWidth(1180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(6)
        self.range_start_label = QLabel(self)
        self.range_stop_label = QLabel(self)
        self.range_start_label.setProperty("ui2Role", "secondary")
        self.range_stop_label.setProperty("ui2Role", "secondary")
        self.range_start = self._frequency_field()
        self.range_stop = self._frequency_field()
        self.apply_range = QPushButton(self)
        self.apply_range.setProperty("ui2Role", "utility-action")
        self.apply_range.clicked.connect(self._request_viewport_range)
        for widget in (self.range_start_label, self.range_start, self.range_stop_label,
                       self.range_stop, self.apply_range):
            header.addWidget(widget)
        header.addStretch(1)
        self.close_button = QPushButton(text("analyzer.close"), self)
        self.close_button.setProperty("ui2Role", "utility-action")
        self.close_button.clicked.connect(self.close_requested)
        header.addWidget(self.close_button)
        root.addLayout(header)
        self.range_error = QLabel(self)
        self.range_error.setProperty("ui2Tone", "error")
        self.range_error.hide()
        root.addWidget(self.range_error)
        self._range_error_key: str | None = None
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        contents = QWidget(self.scroll_area)
        controls_layout = QVBoxLayout(contents)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        controls_layout.addWidget(spectrum_controls)
        controls_layout.addWidget(waterfall_controls)
        self.scroll_area.setWidget(contents)
        root.addWidget(self.scroll_area)
        self.hide()
        self.set_locale(UiLocale.RU)

    def _frequency_field(self) -> QDoubleSpinBox:
        field = QDoubleSpinBox(self)
        field.setProperty("ui2Role", "range-control")
        field.setDecimals(6)
        field.setRange(0.0, 100_000.0)
        field.setMaximumWidth(140)
        return field

    def _request_viewport_range(self) -> None:
        self.viewport_range_requested.emit(self.range_start.value() * 1e6,
                                           self.range_stop.value() * 1e6)

    def set_viewport_range(self, start_hz: float, stop_hz: float) -> None:
        self.range_start.setValue(start_hz / 1e6)
        self.range_stop.setValue(stop_hz / 1e6)
        self.set_range_error(None)

    def set_range_available(self, available: bool) -> None:
        self.range_start.setEnabled(available)
        self.range_stop.setEnabled(available)
        self.apply_range.setEnabled(available)

    def set_range_error(self, key: str | None) -> None:
        if key == self._range_error_key:
            return
        self._range_error_key = key
        self.range_error.setText("" if key is None else text(key))
        self.range_error.setVisible(key is not None)

    def set_locale(self, locale: UiLocale) -> None:
        self.close_button.setText(text("analyzer.close", locale))
        self.range_start_label.setText(text("analyzer.view_range.start", locale))
        self.range_stop_label.setText(text("analyzer.view_range.stop", locale))
        self.apply_range.setText(text("analyzer.view_range.apply", locale))
        self.range_start.setAccessibleName(text("analyzer.view_range.start", locale))
        self.range_stop.setAccessibleName(text("analyzer.view_range.stop", locale))
        self.apply_range.setAccessibleName(text("analyzer.view_range.apply", locale))
        scope = text("analyzer.view_range.scope", locale)
        for widget in (self.range_start, self.range_stop, self.apply_range):
            widget.setToolTip(scope)
        if self._range_error_key is not None:
            self.range_error.setText(text(self._range_error_key, locale))
