"""Compact overlay hosting the existing analyzer presentation controls."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..i18n import UiLocale, text


class AnalyzerDisplayControls(QFrame):
    """Presentation-only owner of detached Spectrum and Waterfall toolbars."""

    close_requested = Signal()

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
        header.addStretch(1)
        self.close_button = QPushButton(text("analyzer.close"), self)
        self.close_button.setProperty("ui2Role", "utility-action")
        self.close_button.clicked.connect(self.close_requested)
        header.addWidget(self.close_button)
        root.addLayout(header)
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

    def set_locale(self, locale: UiLocale) -> None:
        self.close_button.setText(text("analyzer.close", locale))
