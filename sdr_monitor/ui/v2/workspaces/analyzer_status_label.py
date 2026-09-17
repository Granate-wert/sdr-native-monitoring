"""Stable-height accessible status text, independent of changing digit counts."""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QLabel, QSizePolicy


class AnalyzerStatusLabel(QLabel):
    """Reserve two text lines; full text remains in UIA and the tooltip.

    The label may clip overflow rather than steal graph area when telemetry
    grows. Never alter the underlying text or truncate diagnostic provenance.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._reserve_height()

    def _reserve_height(self):
        self.setFixedHeight(2 * self.fontMetrics().lineSpacing() + 4)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._reserve_height()

    def heightForWidth(self, width):
        return self.height()
