"""Theme-aware custom splitter handle for the future spectrum/waterfall layout."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

from ..design.tokens import ThemeId, tokens_for_theme
from ..i18n import text


class SplitterHandle(QSplitterHandle):
    """Small high-contrast grip that remains visible on dark and light panels."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter, *, theme: ThemeId = ThemeId.DARK) -> None:
        super().__init__(orientation, parent)
        self._theme = theme
        self.setAccessibleName(text("component.splitter.handle.name"))

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        tokens = tokens_for_theme(self._theme)
        painter.fillRect(self.rect(), QColor(tokens.colors.panel))
        painter.setPen(QColor(tokens.colors.border))
        center = self.rect().center()
        if self.orientation() is Qt.Orientation.Vertical:
            painter.drawLine(self.rect().left(), center.y(), self.rect().right(), center.y())
        else:
            painter.drawLine(center.x(), self.rect().top(), center.x(), self.rect().bottom())
        painter.end()


class V2Splitter(QSplitter):
    """A splitter that creates only V2 custom handles."""

    def __init__(self, orientation: Qt.Orientation, *, theme: ThemeId = ThemeId.DARK, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._theme = theme
        self.setHandleWidth(8)
        self.setAccessibleName(text("component.splitter.spectrum_waterfall.name"))
        self.setAccessibleDescription(text("component.splitter.spectrum_waterfall.description"))

    def createHandle(self) -> QSplitterHandle:
        return SplitterHandle(self.orientation(), self, theme=self._theme)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        for index in range(1, self.count()):
            handle = self.handle(index)
            if isinstance(handle, SplitterHandle):
                handle.set_theme(theme)
