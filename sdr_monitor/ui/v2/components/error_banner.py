"""Persistent actionable error presentation, not a transient toast substitute."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ErrorBanner(QFrame):
    """Shows cause/recommendation text and an explicit supplied recovery action."""

    action_requested = Signal()

    def __init__(
        self,
        title: str,
        detail: str,
        *,
        action_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("ui2Role", "card")
        self.setProperty("tone", "error")
        self._title_label = QLabel(title, self)
        self._title_label.setProperty("ui2Role", "section-heading")
        self._detail_label = QLabel(detail, self)
        self._detail_label.setProperty("ui2Role", "secondary")
        self._detail_label.setWordWrap(True)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addWidget(self._title_label)
        text_layout.addWidget(self._detail_label)
        self._action = QPushButton(action_text, self)
        self._action.setVisible(bool(action_text))
        self._action.setAccessibleName(action_text)
        self._action.clicked.connect(self.action_requested)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self._action)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)

    def set_action(self, text: str, *, enabled: bool) -> None:
        self._action.setText(text)
        self._action.setVisible(bool(text))
        self._action.setEnabled(enabled)
        self._action.setAccessibleName(text)

    def set_content(self, title: str, detail: str) -> None:
        """Update only the visible error text; recovery ownership stays external."""

        self._title_label.setText(title)
        self._detail_label.setText(detail)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
