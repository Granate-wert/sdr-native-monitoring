"""Screen-anchored cosmetic coverage hatching, shared with render diagnostics."""
from math import ceil, floor

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter, QTransform


def anchor_coverage_pattern(painter: QPainter, local_plot_rect: QRectF) -> QTransform | None:
    """Anchor the 8x8 physical-pixel brush at the plot's top-left pixel.

    Switch to physical device coordinates, returning the previous local->device
    transform for rectangle conversion. Preserve painter state in the caller.
    Clip is installed BEFORE this call. Data axes/DPR cannot distort the tile.
    """
    transform = painter.deviceTransform()
    if not transform.isInvertible() or local_plot_rect.isEmpty():
        return None
    rect = transform.mapRect(local_plot_rect).normalized()
    origin = QPointF(floor(rect.left()), floor(rect.top()))
    painter.resetTransform()
    inverse, valid = painter.deviceTransform().inverted()
    if not valid:
        return None
    painter.setWorldTransform(inverse)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setRenderHint(QPainter.RenderHint.NonCosmeticBrushPatterns, False)
    painter.setBrushOrigin(origin)
    return transform


def coverage_pixel_rect(transform: QTransform, rect: QRectF) -> QRectF:
    """Half-open pixel-centre selection, identical to the GPU scissor contract."""
    mapped = transform.mapRect(rect).normalized()
    left, top = ceil(mapped.left() - .5), ceil(mapped.top() - .5)
    right, bottom = ceil(mapped.right() - .5), ceil(mapped.bottom() - .5)
    return QRectF(left, top, max(0, right-left), max(0, bottom-top))
