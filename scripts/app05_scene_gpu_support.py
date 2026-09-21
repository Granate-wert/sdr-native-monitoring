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


class GpuContextUnavailable(RuntimeError):
    """Only context availability failures allow the explicit CPU fallback."""


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
        self._resources = None
        self._last_resources = None
        self._gpu_failure = None
        self.context_recoveries = 0
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

    def _make_current(self):
        return self._context.makeCurrent(self._surface)

    def scientific_resources(self):
        from PySide6.QtGui import QOpenGLContext
        from scripts.app05_gpu_resources import ScientificGpuResources
        self._guard()
        if QOpenGLContext.currentContext() != self._context:
            raise RuntimeError("scientific resources require this target's current context")
        if self._resources is None:
            self._resources = ScientificGpuResources()
        self._resources._guard()
        return self._resources

    def _release_resources(self):
        if self._resources is not None:
            self._resources.close()
            self._last_resources = self._resources.snapshot()
            self._resources = None

    def recover_context(self):
        """Explicit recovery, never retry or silently restart on every frame.

        Reuses only a still-valid context; real destroyed-context reconstruction
        remains outside this prototype. Old graphics data is released first.
        """
        self._guard()
        if not self.available or not self._context.isValid() or not self._make_current():
            raise GpuContextUnavailable("context recovery unavailable; use CPU fallback")
        try:
            self._release_resources()
            self._release_target()
            self._gpu_failure = None
            self.context_recoveries += 1
        finally:
            self._context.doneCurrent()

    def render_or_cpu(self, extent, panels, background, *, draw=None):
        """Fallback draws current actual scene, never returns last GPU pixels."""
        self._guard()
        try:
            image, elapsed = self.render(extent, panels, background, draw=draw)
            return image, dict(backend="gpu", completed_paint_ms=elapsed, reason=None)
        except GpuContextUnavailable as error:
            retained = (self._extent.pixel_width*self._extent.pixel_height*8 if self._extent else 0)
            retained += self._resources.live_bytes if self._resources is not None else 0
            if retained + extent.pixel_width*extent.pixel_height*4 > extent.target_budget_bytes:
                raise MemoryError("CPU fallback plus retained GPU storage exceeds budget") from error
            image = image_target(extent, background)
            paint_scenes(image, panels)
            return image, dict(backend="cpu", completed_paint_ms=None, reason=str(error))

    def render(self, extent, panels, background, *, draw=None):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLPaintDevice
        from time import perf_counter
        self._guard()
        if self._gpu_failure is not None:
            raise GpuContextUnavailable(self._gpu_failure)
        if not self.available or not self._make_current():
            self._gpu_failure = "GPU context unavailable; caller must select CPU fallback"
            raise GpuContextUnavailable(self._gpu_failure)
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
            if draw is None:
                paint_scenes(self._device, panels)
            else:
                draw(self._device, functions, extent)
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
            self._release_resources()
            self._release_target()
        finally:
            self._context.doneCurrent()
            self._surface.destroy()
            self._closed = True

    def snapshot(self):
        return dict(allocations=self.allocations, releases=self.releases, closed=self._closed,
            scientific_resources=self._resources.snapshot() if self._resources is not None else self._last_resources,
            context_failure=self._gpu_failure, context_recoveries=self.context_recoveries,
            live_targets=int(self._fbo is not None), nominal_peak_target_bytes=self.peak_target_bytes,
            scope="Target accounting only; excludes Qt image/texture caches, source data, driver memory and comparison scratch")
