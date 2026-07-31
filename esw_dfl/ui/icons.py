"""Embedded SVG icon registry with stable semantic identifiers."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


class IconId(StrEnum):
    APP = "app"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_SVGS: dict[IconId, str] = {
    IconId.APP: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect x="4" y="4" width="56" height="56" rx="8" fill="#1f6feb"/><path d="M12 42 C20 20 28 52 36 28 S48 18 54 34" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/><path d="M12 50 H54" stroke="#ffffff" stroke-width="3"/></svg>',
    IconId.INFO: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><circle cx="32" cy="32" r="27" fill="#35c6ff"/><path d="M32 27v18M32 18v2" stroke="#111820" stroke-width="6" stroke-linecap="round"/></svg>',
    IconId.SUCCESS: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><circle cx="32" cy="32" r="27" fill="#3ddc97"/><path d="M18 33l9 9 20-21" fill="none" stroke="#111820" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    IconId.WARNING: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><path d="M32 6l28 50H4z" fill="#ffbd2e"/><path d="M32 24v16M32 48v2" stroke="#111820" stroke-width="6" stroke-linecap="round"/></svg>',
    IconId.ERROR: '<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><circle cx="32" cy="32" r="27" fill="#ff5f56"/><path d="M22 22l20 20M42 22L22 42" stroke="#ffffff" stroke-width="6" stroke-linecap="round"/></svg>',
}


class IconRegistry:
    @staticmethod
    def svg(icon_id: IconId | str) -> str:
        try:
            return _SVGS[IconId(icon_id)]
        except ValueError as error:
            raise KeyError(f"unknown icon: {icon_id}") from error

    @classmethod
    def icon(cls, icon_id: IconId | str, *, size: int = 24) -> QIcon:
        if size <= 0:
            raise ValueError("icon size must be positive")
        renderer = QSvgRenderer(QByteArray(cls.svg(icon_id).encode("utf-8")))
        if not renderer.isValid():
            raise RuntimeError(f"invalid SVG icon: {icon_id}")
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)
