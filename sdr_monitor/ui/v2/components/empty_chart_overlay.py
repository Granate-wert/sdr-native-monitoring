"""Meaningful empty chart state with explicit, injected actions."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..i18n import text


class EmptyChartOverlay(QFrame):
    """Avoids a blank spectrum region without inferring any device state."""

    primary_requested = Signal()
    secondary_requested = Signal()

    def __init__(
        self,
        title: str | None = None,
        detail: str | None = None,
        *,
        primary_text: str | None = None,
        secondary_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        title = text("component.empty_chart.default.title") if title is None else title
        detail = text("component.empty_chart.default.detail") if detail is None else detail
        primary_text = text("component.empty_chart.default.primary") if primary_text is None else primary_text
        secondary_text = text("component.empty_chart.default.secondary") if secondary_text is None else secondary_text
        self.setProperty("ui2Role", "panel")
        title_label = QLabel(title, self)
        title_label.setProperty("ui2Role", "workspace-heading")
        detail_label = QLabel(detail, self)
        detail_label.setProperty("ui2Role", "secondary")
        detail_label.setWordWrap(True)
        self._primary = QPushButton(primary_text, self)
        self._primary.setProperty("ui2Role", "primary-action")
        self._primary.clicked.connect(self.primary_requested)
        self._secondary = QPushButton(secondary_text, self)
        self._secondary.clicked.connect(self.secondary_requested)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(self._primary)
        buttons.addWidget(self._secondary)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(title_label)
        layout.addWidget(detail_label)
        layout.addLayout(buttons)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
