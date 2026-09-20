"""Bounded experimental device-space strokes and detached coverage primitives.

Qt generates cap/join/dash outlines, not pixels. GPU uses nonzero winding, not
driver wide lines, and rejects geometry that could overflow its 8-bit stencil.
"""
from math import ceil, hypot, isfinite

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPathStroker, QTransform


def stroke_polygons(layer, extent, budget_bytes=32*1024*1024):
    if layer.path is None or layer.pen is None or not layer.pen.isCosmetic():
        raise ValueError("prototype requires a cosmetic path pen")
    pen = layer.pen
    width = pen.widthF() or 1.
    if not isfinite(width) or not 0 < width <= 16 or not 0 <= pen.miterLimit() <= 8:
        raise ValueError("unsupported stroke width/miter")
    count = layer.path.elementCount()
    if count > 65536:
        raise MemoryError("stroke input point bound exceeded")
    if any(layer.path.elementAt(i).isCurveTo() for i in range(count)):
        raise ValueError("scientific input must be finite polyline subpaths")
    matrix = layer.matrix() * QTransform.fromScale(extent.dpr, extent.dpr)
    path = matrix.map(layer.path)
    previous = None
    length = 0.
    for i in range(path.elementCount()):
        point = path.elementAt(i)
        if not all(isfinite(v) and abs(v) <= 8*max(extent.pixel_width, extent.pixel_height) for v in (point.x, point.y)):
            raise ValueError("unbounded/off-target stroke geometry")
        if not point.isMoveTo() and previous is not None:
            length += hypot(point.x-previous.x, point.y-previous.y)
        previous = point
    dash = pen.dashPattern()
    if dash and min(dash) * width < 1:
        raise ValueError("subpixel dash explosion not admitted")
    # Conservative admission estimate bounds input and dash expansion BEFORE Qt.
    estimate = count*32 + (ceil(length/(min(dash)*width))*32 if dash else 0) + 64
    if estimate*32 > budget_bytes:
        raise MemoryError("stroke expansion budget exceeded before outlining")
    stroker = QPainterPathStroker(pen)
    stroker.setWidth(width)
    outline = stroker.createStroke(path)
    if outline.elementCount()*32 > budget_bytes:
        raise MemoryError("Qt stroke output exceeds declared bound")
    contours = outline.toSubpathPolygons()
    vertices = sum(polygon.size() for polygon in contours)
    if vertices*256 > budget_bytes:
        raise MemoryError("stroke polygon/upload scratch budget exceeded")
    # A vertical ray cannot have a winding magnitude greater than half the
    # active outline-edge count. Conservative bound prevents modulo-256 holes.
    events = np.empty((vertices*2, 2), dtype=np.float64)
    used = 0
    for polygon in contours:
        for index in range(polygon.size()):
            first, second = polygon.at(index), polygon.at((index+1) % polygon.size())
            left, right = sorted((first.x(), second.x()))
            if left < right:
                events[used:used+2] = ((left, 1), (right, -1))
                used += 2
    selected = events[:used]
    order = np.lexsort((selected[:, 1], selected[:, 0]))  # ending edges first at identical X
    maximum = int(np.max(np.cumsum(selected[order, 1]), initial=0))
    if maximum >= 254:
        raise ValueError("stroke winding could overflow 8-bit stencil")
    return contours, dict(vertices=vertices, nominal_geometry_bytes=vertices*256,
                          winding_edge_bound=maximum, input_elements=count, device_width=width)


def normalized_polygon(polygon, extent):
    # Frequencies were mapped in float64 before this bounded screen conversion.
    return np.asarray([(2*p.x()/extent.pixel_width-1, 1-2*p.y()/extent.pixel_height)
                       for p in polygon], dtype=np.float32)


def pattern_rows(style):
    """Qt's canonical 8x8 cosmetic brush mask; generated once per local draw."""
    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.fillRect(QRectF(0, 0, 8, 8), QBrush(QColor("white"), Qt.BrushStyle(style)))
    painter.end()
    return tuple(sum(int(image.pixelColor(x, y).alpha() != 0) << x for x in range(8)) for y in range(8))
