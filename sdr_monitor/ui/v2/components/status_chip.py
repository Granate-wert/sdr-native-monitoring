"""Compact semantic status with explicit text, icon and accessible detail."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from ..design.icons import V2IconId, themed_icon
from ..design.tokens import StatusTone, ThemeId, tokens_for_theme


class StatusChipV2(QFrame):
    """A status item whose meaning never depends only on its colour."""

    detail_requested = Signal()

    def __init__(
        self,
        text: str,
        *,
        tone: StatusTone = StatusTone.NEUTRAL,
        detail: str = "",
        theme: ThemeId = ThemeId.DARK,
        parent: QFrame | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("statusChipV2")
        self.setProperty("ui2Role", "status-chip")
        self._icon = QLabel(self)
        self._text = QLabel(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(4)
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        self.set_tone(tone)
        self.set_text(text, detail=detail)

    @property
    def tone(self) -> StatusTone:
        return StatusTone(self.property("tone"))

    @property
    def text(self) -> str:
        return self._text.text()

    def set_tone(self, tone: StatusTone) -> None:
        tokens = tokens_for_theme(self._theme)
        icon_id = {
            StatusTone.NEUTRAL: V2IconId.INFO,
            StatusTone.INFO: V2IconId.INFO,
            StatusTone.SUCCESS: V2IconId.SUCCESS,
            StatusTone.WARNING: V2IconId.WARNING,
            StatusTone.ERROR: V2IconId.ERROR,
        }[tone]
        self.setProperty("tone", tone.value)
        self._icon.setPixmap(themed_icon(icon_id, tokens.colors.status(tone), size=14).pixmap(14, 14))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.set_tone(self.tone)

    def set_text(self, text: str, *, detail: str = "") -> None:
        self._text.setText(text)
        self.setToolTip(detail or text)
        self.setAccessibleName(text)
        self.setAccessibleDescription(detail or text)

    def mousePressEvent(self, event) -> None:
        self.detail_requested.emit()
        super().mousePressEvent(event)
