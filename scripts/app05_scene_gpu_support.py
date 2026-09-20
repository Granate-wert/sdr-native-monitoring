"""Experimental shared Qt scene target; never imported by product code.

One context/FBO for spectrum, density and waterfall, CPU reference/fallback.
No source owner, acquisition queue, texture history or second active pipeline.
Explicit logical/device geometry; allocated targets are bounded, not driver RSS.
"""
from dataclasses import dataclass
from math import ceil, isfinite
from typing import Any


@dataclass(frozen=True)
class SceneExtent:
    width: int
    height: int
    dpr: float = 1.
    target_budget_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (self.width, self.height)):
            raise ValueError("logical scene dimensions must be positive integers")
        if isinstance(self.dpr, bool) or not isfinite(self.dpr) or not 1 <= self.dpr <= 2:
            raise ValueError("prototype DPR must be finite in 1..2")
        if self.pixel_width > 8192 or self.pixel_height > 4320:
            raise ValueError("physical scene exceeds 8192x4320")
        if isinstance(self.target_budget_bytes, bool) or not isinstance(self.target_budget_bytes, int):
            raise ValueError("target budget must be integer bytes")
        if self.target_budget_bytes < self.nominal_target_bytes:
            raise ValueError("scene target budget exceeded")

    @property
    def pixel_width(self):
        return ceil(self.width * self.dpr)

    @property
    def pixel_height(self):
        return ceil(self.height * self.dpr)

    @property
    def nominal_target_bytes(self):
        # RGBA8+D24S8 GPU target, CPU reference, readback and transient
        # readback format conversion, each CPU image at four bytes/pixel.
        return self.pixel_width * self.pixel_height * 20


def image_target(extent, background):
    from PySide6.QtGui import QImage
    image = QImage(extent.pixel_width, extent.pixel_height, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise MemoryError("CPU scene target allocation failed")
    image.setDevicePixelRatio(extent.dpr)
    image.setDotsPerMeterX(3780)
    image.setDotsPerMeterY(3780)
    image.fill(background)
    return image


def paint_scenes(target, panels):
    """Draw real V2 GraphicsScenes into one target, with no scientific remapping.

    Panels contain (QGraphicsView, logical target QRectF). Source mapping follows
    the current real viewport; no invented frequency/time coordinates or bins.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    painter = QPainter(target)
    if not painter.isActive():
        raise RuntimeError("scene painter did not begin")
    try:
        painter.setRenderHints(QPainter.RenderHint.TextAntialiasing)
        for graphics, rect in panels:
            painter.save()
            try:
                painter.setClipRect(rect)
                painter.fillRect(rect, graphics.backgroundBrush())
                source = graphics.mapToScene(graphics.viewport().rect()).boundingRect()
                graphics.scene().render(painter, rect, source, Qt.AspectRatioMode.IgnoreAspectRatio)
            finally:
                painter.restore()
    finally:
        painter.end()


def compare_images(reference, candidate):
    """Exact comparison, row-bounded scratch; do not allocate full int16 deltas."""
    import hashlib
    import numpy as np
    if reference.size() != candidate.size() or reference.format() != candidate.format():
        raise ValueError("comparison requires identical physical dimensions and formats")
    width, height = reference.width(), reference.height()
    left = np.frombuffer(reference.constBits(), np.uint8).reshape(height, reference.bytesPerLine())[:, :width * 4]
    right = np.frombuffer(candidate.constBits(), np.uint8).reshape(height, candidate.bytesPerLine())[:, :width * 4]
    different = maximum = greater_one = greater_four = 0
    bounds = None
    for row in range(height):
        a, b = left[row].reshape(width, 4), right[row].reshape(width, 4)
        indices = np.flatnonzero(np.any(a != b, axis=1))
        if indices.size:
            different += indices.size
            first, last = int(indices[0]), int(indices[-1])
            bounds = [first, row, last, row] if bounds is None else [min(bounds[0], first), bounds[1], max(bounds[2], last), row]
            error = np.max(np.abs(a.astype(np.int16) - b.astype(np.int16)), axis=1)
            maximum = max(maximum, int(error.max()))
            greater_one += int(np.count_nonzero(error > 1))
            greater_four += int(np.count_nonzero(error > 4))
    return dict(equal=different == 0, different_pixels=int(different), total_pixels=width * height,
        max_channel_error=maximum, difference_bounds=bounds,
        pixels_error_gt_one=greater_one, pixels_error_gt_four=greater_four,
        reference_sha256=hashlib.sha256(reference.constBits()).hexdigest(),
        candidate_sha256=hashlib.sha256(candidate.constBits()).hexdigest())


class SceneGpuTarget:
    """GUI-thread/context-bound experimental owner with at most ONE FBO."""
    def __init__(self):
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
        import threading
        self._thread = threading.get_ident()
        self._surface = QOffscreenSurface()
        self._surface.create()
        self._context = QOpenGLContext()
        self._fbo = self._device = self._extent = None
        self._closed = False
        self.allocations = self.releases = 0
        self.peak_target_bytes = 0
        created = self._context.create()
        self.available = bool(created and self._context.makeCurrent(self._surface))
        self.info: dict[str, Any] = dict(context_created=created, available=self.available)
        if self.available:
            functions = self._context.functions()
            self.info.update(renderer=functions.glGetString(0x1F01), vendor=functions.glGetString(0x1F00),
                version=functions.glGetString(0x1F02))
            self._context.doneCurrent()

    def _guard(self):
        import threading
        if threading.get_ident() != self._thread:
            raise RuntimeError("GPU scene owner used from another thread")
        if self._closed:
            raise RuntimeError("GPU scene owner is closed")

    def _release_target(self):
        if self._fbo is not None:
            self._fbo.release()
            self._device = None
            self._fbo = None
            self._extent = None
            self.releases += 1

    def render(self, extent, panels, background):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLPaintDevice
        from time import perf_counter
        self._guard()
        if not self.available or not self._context.makeCurrent(self._surface):
            raise RuntimeError("GPU context unavailable; caller must select CPU fallback")
        try:
            if extent != self._extent:
                # Destroy old before allocating replacement: no double-FBO peak.
                self._release_target()
                fmt = QOpenGLFramebufferObjectFormat()
                fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                fmt.setInternalTextureFormat(0x8058)
                fbo = QOpenGLFramebufferObject(QSize(extent.pixel_width, extent.pixel_height), fmt)
                if not fbo.isValid():
                    raise MemoryError("GPU scene target allocation failed")
                self._fbo = fbo
                self._device = QOpenGLPaintDevice(QSize(extent.pixel_width, extent.pixel_height))
                self._device.setDevicePixelRatio(extent.dpr)
                self._device.setDotsPerMeterX(3780)
                self._device.setDotsPerMeterY(3780)
                self._extent = extent
                self.allocations += 1
                self.peak_target_bytes = max(self.peak_target_bytes, extent.nominal_target_bytes)
            if self._fbo is None or self._device is None:
                raise RuntimeError("GPU scene target not initialized")
            if not self._fbo.bind():
                raise RuntimeError("GPU scene target bind failed")
            functions = self._context.functions()
            functions.glViewport(0, 0, extent.pixel_width, extent.pixel_height)
            functions.glDisable(0x0C11)
            functions.glClearColor(background.redF(), background.greenF(), background.blueF(), 1.)
            functions.glClear(0x4000 | 0x0100 | 0x0400)
            functions.glFinish()
            begin = perf_counter()
            paint_scenes(self._device, panels)
            functions.glFinish()
            elapsed_ms = (perf_counter() - begin) * 1000
            image = self._fbo.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(extent.dpr)
            error = functions.glGetError()
            if error:
                raise RuntimeError(f"OpenGL error {error}")
            return image, elapsed_ms
        finally:
            self._context.doneCurrent()

    def close(self):
        if self._closed:
            return
        self._guard()
        if self.available and not self._context.makeCurrent(self._surface):
            raise RuntimeError("cannot release GL resources without owning context")
        try:
            self._release_target()
        finally:
            self._context.doneCurrent()
            self._surface.destroy()
            self._closed = True

    def snapshot(self):
        return dict(allocations=self.allocations, releases=self.releases, closed=self._closed,
            live_targets=int(self._fbo is not None), nominal_peak_target_bytes=self.peak_target_bytes,
            scope="Target accounting only; excludes Qt image/texture caches, source data, driver memory and comparison scratch")
