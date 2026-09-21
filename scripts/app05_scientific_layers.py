"""Experimental detached scientific layers; no product imports or persistent cache.

Pixels are already level/LUT mapped by the accepted V2 ImageItem. We do not
interpret dB, probability, FFT bins or waterfall time again. Image geometry
preserves both negative-axis directions. Paths preserve finite subpaths.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QTransform


@dataclass(frozen=True)
class CoverageRect:
    rect: tuple[float, float, float, float]
    rgba: tuple[int, int, int, int]
    style: int
    state: int


@dataclass(frozen=True)
class ScientificLayer:
    name: str
    panel: int
    z: float
    clip: tuple[float, float, float, float]
    transform: tuple[float, float, float, float, float, float]
    opacity: float
    image: Any = None
    local_rect: tuple[float, float, float, float] | None = None
    path: Any = None
    pen: Any = None
    coverage: tuple[CoverageRect, ...] = ()
    pattern_rect: tuple[float, float, float, float] | None = None

    def matrix(self):
        return QTransform(*self.transform)

    def target_rect(self):
        if self.local_rect is None:
            raise ValueError("only image layers have an image rectangle")
        # Do not normalize: negative height defines waterfall direction.
        rect = QRectF(*self.local_rect)
        a, b = self.matrix().map(rect.topLeft()), self.matrix().map(rect.bottomRight())
        return a.x(), a.y(), b.x() - a.x(), b.y() - a.y()


@dataclass(frozen=True)
class ScientificLayers:
    layers: tuple[ScientificLayer, ...]
    retained_bytes: int
    allocation_limit: int
    omitted: tuple[str, ...] = ("chrome", "grid", "markers", "labels", "band masks")


def _mapping(item, graphics, target):
    source = graphics.mapToScene(graphics.viewport().rect()).boundingRect()
    panel = QTransform()
    panel.translate(target.x(), target.y())
    panel.scale(target.width() / source.width(), target.height() / source.height())
    panel.translate(-source.x(), -source.y())
    matrix = item.sceneTransform() * panel
    if not matrix.isAffine() or matrix.m12() != 0 or matrix.m21() != 0:
        raise ValueError("prototype supports axis-aligned finite scientific geometry only")
    values = (matrix.m11(), matrix.m12(), matrix.m21(), matrix.m22(), matrix.dx(), matrix.dy())
    if not all(isfinite(v) for v in values) or matrix.m11() == 0 or matrix.m22() == 0:
        raise ValueError("degenerate scientific geometry")
    clip = QRectF(target)
    parent = item.parentItem()
    while parent is not None:
        if parent.flags() & parent.GraphicsItemFlag.ItemClipsChildrenToShape:
            shape = parent.shape()
            bounds = shape.boundingRect()
            rectangle = QPainterPath()
            rectangle.addRect(bounds)
            if shape != rectangle:
                raise ValueError("nonrectangular ancestor clip requires explicit support")
            clip = clip.intersected(panel.mapRect(parent.mapRectToScene(bounds)))
        parent = parent.parentItem()
    return values, (clip.x(), clip.y(), clip.width(), clip.height())


def detach_layers(scene, waterfall, panels, limit_bytes=64 * 1024 * 1024):
    """Synchronous GUI snapshot only; no references to widgets/source publications.

    Budget checked BEFORE each detached payload allocation. QImage format
    conversion transient is conservatively allowed alongside retained payload.
    Opaque Qt caches and the existing scene are explicitly outside this budget.
    """
    layers = []
    retained = 0
    if isinstance(limit_bytes, bool) or not isinstance(limit_bytes, int) or limit_bytes <= 0:
        raise ValueError("layer allocation limit must be positive integer bytes")
    image_items = [("persistence", 0, scene._persistence.image_item)]
    image_items += [(f"waterfall-{i}", 1, item) for i, item in enumerate(waterfall.image_items)]
    for name, index, item in image_items:
        if not item.isVisible() or item.image is None:
            continue
        if item._renderRequired or item.qimage is None:
            raise RuntimeError("detach only already-rendered accepted images; do not hide preparation work")
        if item.paintMode not in (None, QPainter.CompositionMode.CompositionMode_SourceOver) or item.border is not None:
            raise ValueError("unsupported image composition or border")
        required = item.qimage.width() * item.qimage.height() * 4
        if retained + 2 * required > limit_bytes:
            raise MemoryError("scientific layer image budget exceeded before copy")
        pixels = item.qimage.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied).copy()
        if pixels.isNull():
            raise MemoryError("scientific image copy failed")
        retained += pixels.sizeInBytes()
        matrix, clip = _mapping(item, *panels[index])
        rect = item.boundingRect()
        layers.append(ScientificLayer(name, index, item.zValue(), clip, matrix, item.effectiveOpacity(),
            image=pixels, local_rect=(rect.x(), rect.y(), rect.width(), rect.height())))
    curves = [(kind.value, item) for kind, item in scene._curves.items()]
    curves.append(("previous-sweep", scene.sweep_coverage.history))
    for name, item in curves:
        curve = item.curve
        if not item.isVisible() or not curve.isVisible() or curve.xData is None:
            continue
        if curve.opts.get("fillLevel") is not None or curve.opts.get("shadowPen") is not None:
            raise ValueError("unsupported curve fill/shadow")
        path = curve.getPath()
        # QPainterPath elements: conservative 32 bytes per x/y/type element.
        required = path.elementCount() * 32
        if retained + required > limit_bytes:
            raise MemoryError("scientific path budget exceeded before copy")
        matrix, clip = _mapping(curve, *panels[0])
        if curve.opts.get("antialias"):
            raise ValueError("antialias curve needs separate quality contract")
        retained += required
        layers.append(ScientificLayer(name, 0, item.zValue(), clip, matrix, curve.effectiveOpacity(),
            path=QPainterPath(path), pen=QPen(curve.opts["pen"])))
    strip = getattr(scene.sweep_coverage, "strip", None)
    if strip is not None and strip.isVisible() and strip.runs:
        from sdr_monitor.ui.v2.design import ThemeId, tokens_for_theme
        from sdr_monitor.ui.v2.spectrum.sweep_coverage import CURRENT, MISSING, PREVIOUS
        colors = tokens_for_theme(strip.theme).colors
        rects = []
        if len(strip.runs) > 2048 or retained + len(strip.runs) * 2 * 128 + 32 > limit_bytes:
            raise MemoryError("coverage descriptor budget exceeded")
        for left, right, state in strip.runs:
            color = QColor(colors.success if state == CURRENT else
                           colors.secondary_text if state == PREVIOUS else colors.warning)
            style = (Qt.BrushStyle.SolidPattern if state == CURRENT else
                     Qt.BrushStyle.HorPattern if state == PREVIOUS else
                     Qt.BrushStyle.BDiagPattern if state == MISSING else Qt.BrushStyle.DiagCrossPattern)
            r = strip.rect
            rects.append(CoverageRect((left, r.bottom()-r.height()*.085, right-left, r.height()*.035),
                                     (color.red(), color.green(), color.blue(), color.alpha()), style.value, state))
            if state & MISSING:
                color.setAlpha(45 if strip.theme is not ThemeId.HIGH_CONTRAST else 100)
                rects.append(CoverageRect((left, r.top(), right-left, r.height()*.90),
                                         (color.red(), color.green(), color.blue(), color.alpha()), Qt.BrushStyle.BDiagPattern.value, state))
        matrix, clip = _mapping(strip, *panels[0])
        local_clip = QTransform(*matrix).mapRect(strip.rect).intersected(QRectF(*clip))
        clip = local_clip.x(), local_clip.y(), local_clip.width(), local_clip.height()
        layers.append(ScientificLayer("coverage", 0, strip.zValue(), clip, matrix, strip.effectiveOpacity(),
                                      coverage=tuple(rects), pattern_rect=strip.rect.getRect()))
        retained += len(rects) * 128 + 32  # detached pattern anchor rectangle
    return ScientificLayers(tuple(sorted(layers, key=lambda layer: (layer.panel, layer.z))), retained, limit_bytes)


def paint_layers(device, bundle, *, images=True, curves=True):
    """CPU oracle / explicit Qt path drawing; no QGraphicsScene traversal."""
    painter = QPainter(device)
    if not painter.isActive():
        raise RuntimeError("scientific painter did not begin")
    try:
        for layer in bundle.layers:
            if (layer.image is not None and not images) or (layer.image is None and not curves):
                continue
            painter.save()
            try:
                painter.setClipRect(QRectF(*layer.clip))
                painter.setOpacity(layer.opacity)
                painter.setTransform(layer.matrix())
                if layer.image is not None:
                    painter.drawImage(QRectF(*layer.local_rect), layer.image)
                elif layer.coverage:
                    from sdr_monitor.ui.v2.spectrum.coverage_pattern import anchor_coverage_pattern, coverage_pixel_rect
                    transform = None
                    if layer.pattern_rect is not None:
                        transform = anchor_coverage_pattern(painter, QRectF(*layer.pattern_rect))
                    painter.setPen(Qt.PenStyle.NoPen)
                    for rect in layer.coverage:
                        painter.setBrush(QBrush(QColor(*rect.rgba), Qt.BrushStyle(rect.style)))
                        bounds = QRectF(*rect.rect)
                        painter.drawRect(bounds if transform is None else coverage_pixel_rect(transform, bounds))
                else:
                    painter.setPen(layer.pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(layer.path)
            finally:
                painter.restore()
    finally:
        painter.end()


def layer_metadata(bundle):
    import hashlib
    return dict(retained_bytes=bundle.retained_bytes, limit_bytes=bundle.allocation_limit,
        omitted=list(bundle.omitted), layers=[dict(name=layer.name, panel=layer.panel, z=layer.z,
            clip=layer.clip, transform=layer.transform, opacity=layer.opacity,
            image_size=[layer.image.width(), layer.image.height()] if layer.image is not None else None,
            image_sha256=hashlib.sha256(layer.image.constBits()).hexdigest() if layer.image is not None else None,
            target_rect=layer.target_rect() if layer.image is not None else None,
            path_elements=layer.path.elementCount() if layer.path is not None else 0,
            coverage_rects=[dict(rect=r.rect, rgba=r.rgba, style=r.style, state=r.state) for r in layer.coverage],
            pattern_rect=layer.pattern_rect,
            pen=dict(width=layer.pen.widthF(), cosmetic=layer.pen.isCosmetic(),
                     cap=layer.pen.capStyle().value, join=layer.pen.joinStyle().value,
                     dash=list(layer.pen.dashPattern())) if layer.pen is not None else None)
        for layer in bundle.layers])


def paint_texel_oracle(target, bundle):
    """Independent float64 pixel-centre nearest-texel oracle, bounded row scratch.

    Not a proposed CPU renderer. No QPainter sampling/interpolation. Byte
    premultiplied source-over is defined as src + round(dst*(255-alpha)/255).
    It can distinguish Qt raster quantization from shader texture errors.
    """
    import numpy as np
    from math import ceil, floor
    if target.format() != QImage.Format.Format_RGBA8888_Premultiplied:
        raise ValueError("texel oracle requires explicit RGBA premultiplied target")
    dpr = target.devicePixelRatio()
    output = np.frombuffer(target.bits(), np.uint8).reshape(target.height(), target.bytesPerLine())[:, :target.width()*4]
    output = output.reshape(target.height(), target.width(), 4)
    for layer in bundle.layers:
        if layer.image is None:
            continue
        if layer.opacity != 1:
            raise ValueError("oracle opacity modulation not implemented")
        x, y, width, height = layer.target_rect()
        cx, cy, cw, ch = layer.clip
        left, right = max(min(x, x+width), cx), min(max(x, x+width), cx+cw)
        top, bottom = max(min(y, y+height), cy), min(max(y, y+height), cy+ch)
        x0, x1 = max(0, ceil(left*dpr-.5)), min(target.width(), ceil(right*dpr-.5))
        y0, y1 = max(0, ceil(top*dpr-.5)), min(target.height(), ceil(bottom*dpr-.5))
        if x1 <= x0 or y1 <= y0:
            continue
        image = layer.image
        pixels = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*4]
        pixels = pixels.reshape(image.height(), image.width(), 4)
        columns = np.floor(((np.arange(x0, x1, dtype=np.float64)+.5)/dpr-x)*image.width()/width).astype(np.int64)
        columns = np.clip(columns, 0, image.width()-1)
        for row in range(y0, y1):
            index = min(image.height()-1, max(0, floor(((row+.5)/dpr-y)*image.height()/height)))
            source = pixels[index, columns].astype(np.uint16)
            destination = output[row, x0:x1].astype(np.uint16)
            output[row, x0:x1] = np.minimum(255, source + (destination*(255-source[:, 3:4])+127)//255).astype(np.uint8)
