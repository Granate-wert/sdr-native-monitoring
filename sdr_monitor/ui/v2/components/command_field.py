"""Compact labelled command input with no domain-specific validation."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget


class CommandField(QWidget):
    """Presentation-only labelled field; later workspaces own validation."""

    value_changed = Signal(str)

    def __init__(
        self,
        label: str,
        *,
        value: str = "",
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = QLabel(label, self)
        self._input = QLineEdit(value, self)
        self._input.setProperty("ui2Role", "command-field")
        self._input.setPlaceholderText(placeholder)
        self._input.setAccessibleName(label)
        self._input.setAccessibleDescription(placeholder or label)
        self._input.textChanged.connect(self.value_changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._label)
        layout.addWidget(self._input, 1)
        self.setAccessibleName(label)
        self.setAccessibleDescription(placeholder or label)

    @property
    def value(self) -> str:
        return self._input.text()

    def set_value(self, value: str) -> None:
        self._input.setText(value)

    @property
    def input(self) -> QLineEdit:
        return self._input
