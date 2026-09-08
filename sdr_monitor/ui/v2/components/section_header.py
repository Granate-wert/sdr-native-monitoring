"""Simple heading hierarchy for compact V2 panels."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class SectionHeader(QWidget):
    """One semantic title and optional contextual, non-measurement detail."""

    def __init__(self, title: str, subtitle: str = "", *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._heading = QLabel(title, self)
        self._heading.setProperty("ui2Role", "section-heading")
        self._subtitle = QLabel(subtitle, self)
        self._subtitle.setProperty("ui2Role", "secondary")
        self._subtitle.setWordWrap(True)
        self._subtitle.setMinimumWidth(0)
        self._subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._heading)
        layout.addWidget(self._subtitle, 1)
        layout.addStretch(1)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self.setAccessibleDescription(text)
