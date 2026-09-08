"""Non-modal temporary panel that closes by Esc or loss of focus."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QWidget


class ContextPopover(QFrame):
    """Compact focusable popup without a nested event loop or modal dialog."""

    def __init__(self, title: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setProperty("ui2Role", "card")
        self.setProperty("ui2FocusRing", True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)
        heading = QLabel(title, self)
        heading.setProperty("ui2Role", "section-heading")
        self._layout.addWidget(heading)
        self.setAccessibleName(title)

    def add_content(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def open_next_to(self, anchor: QWidget) -> None:
        self.adjustSize()
        self.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        self.show()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.hide()
        super().focusOutEvent(event)
