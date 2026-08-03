"""Embedded, licence-free SVG icons with semantic names."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


class IconId(StrEnum):
    APP = "app"
    HOME = "home"
    LIVE = "live"
    SWEEP = "sweep"
    CALIBRATION = "calibration"
    RECORDING = "recording"
    DIAGNOSTICS = "diagnostics"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_SVG = {
    IconId.APP: '<svg viewBox="0 0 64 64"><rect x="4" y="4" width="56" height="56" rx="7" fill="#3CA6FF"/><path d="M12 42C20 20 28 52 36 28S48 18 54 34" fill="none" stroke="white" stroke-width="5"/><path d="M12 51h42" stroke="white" stroke-width="3"/></svg>',
    IconId.HOME: '<svg viewBox="0 0 24 24"><path d="M3 11l9-8 9 8v10H3z" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.LIVE: '<svg viewBox="0 0 24 24"><path d="M2 14c3-8 5 5 8-4s5 6 7-2 3-1 5-4" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.SWEEP: '<svg viewBox="0 0 24 24"><path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" stroke-width="2"/><path d="M6 4v16" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.CALIBRATION: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7v5l3 3" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.RECORDING: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="2" fill="currentColor"/></svg>',
    IconId.DIAGNOSTICS: '<svg viewBox="0 0 24 24"><path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.INFO: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6m0-10v1" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.SUCCESS: '<svg viewBox="0 0 24 24"><path d="M4 12l5 5 11-11" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.WARNING: '<svg viewBox="0 0 24 24"><path d="M12 3l10 18H2z" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 9v5m0 3v1" stroke="currentColor" stroke-width="2"/></svg>',
    IconId.ERROR: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 8l8 8m0-8l-8 8" stroke="currentColor" stroke-width="2"/></svg>',
}


class IconRegistry:
    @staticmethod
    def svg(icon_id: IconId | str) -> str:
        return _SVG[IconId(icon_id)]

    @classmethod
    def icon(cls, icon_id: IconId | str, *, size: int = 20) -> QIcon:
        if size <= 0:
            raise ValueError("icon size must be positive")
        renderer = QSvgRenderer(QByteArray(cls.svg(icon_id).encode()))
        if not renderer.isValid():
            raise RuntimeError(f"Invalid SVG icon: {icon_id}")
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return QIcon(image)
