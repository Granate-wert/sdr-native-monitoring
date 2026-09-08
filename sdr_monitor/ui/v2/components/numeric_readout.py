"""Accessible fixed-width numeric measurement readout."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QWidget


class NumericReadout(QFrame):
    """Displays a provided value and unit without changing its semantics."""

    def __init__(
        self,
        label: str,
        value: str,
        unit: str,
        *,
        detail: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("ui2Role", "card")
        self._label = QLabel(label, self)
        self._label.setProperty("ui2Role", "secondary")
        self._value = QLabel(self)
        self._value.setProperty("ui2Role", "numeric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        layout.addWidget(self._label)
        layout.addWidget(self._value)
        self.set_value(value, unit, detail=detail)

    def set_value(self, value: str, unit: str, *, detail: str = "") -> None:
        rendered = f"{value} {unit}".strip()
        self._value.setText(rendered)
        self.setAccessibleName(f"{self._label.text()}: {rendered}")
        self.setAccessibleDescription(detail or self.accessibleName())
