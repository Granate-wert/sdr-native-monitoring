"""Small theme-coloured SVG icons for UI V2 controls."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


class V2IconId(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CHEVRON = "chevron"
    CLOSE = "close"
    NAVIGATION = "navigation"


_PATHS: dict[V2IconId, str] = {
    V2IconId.INFO: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    V2IconId.SUCCESS: '<path d="M4 12l5 5 11-11"/>',
    V2IconId.WARNING: '<path d="M12 3l10 18H2z"/><path d="M12 9v5m0 3v1"/>',
    V2IconId.ERROR: '<circle cx="12" cy="12" r="9"/><path d="M8 8l8 8m0-8l-8 8"/>',
    V2IconId.CHEVRON: '<path d="M8 10l4 4 4-4"/>',
    V2IconId.CLOSE: '<path d="M6 6l12 12m0-12L6 18"/>',
    V2IconId.NAVIGATION: '<path d="M4 6h16M4 12h16M4 18h16"/>',
}


def svg_for_icon(icon_id: V2IconId | str, color: str) -> str:
    """Resolve ``currentColor`` at SVG creation, not after rasterization."""

    selected = V2IconId(icon_id)
    return (
        '<svg viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{_PATHS[selected]}</svg>"
    )


def themed_icon(icon_id: V2IconId | str, color: str, *, size: int = 20) -> QIcon:
    """Return a visible, explicitly-coloured raster icon for one target size."""

    if size <= 0:
        raise ValueError("icon size must be positive")
    renderer = QSvgRenderer(QByteArray(svg_for_icon(icon_id, color).encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError(f"invalid UI V2 SVG icon: {icon_id}")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
