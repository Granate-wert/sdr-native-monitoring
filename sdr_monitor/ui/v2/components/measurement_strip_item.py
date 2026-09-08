"""Compact labelled measurement for the later V2 bottom strip."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class MeasurementStripItem(QFrame):
    """Displays supplied values; unit conversion stays outside the widget."""

    def __init__(self, label: str, value: str, *, detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("ui2Role", "status-chip")
        label_widget = QLabel(label, self)
        label_widget.setProperty("ui2Role", "secondary")
        self._value = QLabel(value, self)
        self._value.setProperty("ui2Role", "strip-numeric")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)
        layout.addWidget(label_widget)
        layout.addWidget(self._value)
        self.setAccessibleName(f"{label}: {value}")
        self.setAccessibleDescription(detail or self.accessibleName())

    def set_value(self, value: str) -> None:
        self._value.setText(value)
