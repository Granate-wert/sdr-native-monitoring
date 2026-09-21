"""Experimental shared Qt scene target; never imported by product code.

One context/FBO for spectrum, density and waterfall, CPU reference/fallback.
No source owner, acquisition queue, texture history or second active pipeline.
Explicit logical/device geometry; allocated targets are bounded, not driver RSS.
"""
from dataclasses import dataclass
from math import ceil, isfinite
from typing import Any
from scripts.app05_gpu_errors import GpuOperationError


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
        self._context: QOpenGLContext | None = QOpenGLContext()
        self._fbo = self._device = self._extent = None
        self._closed = False
        self._resources = None
        self._last_resources = None
        self._gpu_failure = None
        self._cleanup_error = None
        self._last_cleanup_warning = None
        self.abandoned_targets = 0
        self.context_generation = 0
        self.context_destructions = 0
        self._rendering = False
        self.context_recoveries = 0
        self.allocations = self.releases = 0
        self.peak_target_bytes = 0
        created = self._context.create()
        self.available = bool(created and self._context.makeCurrent(self._surface))
        self.info: dict[str, Any] = dict(context_created=created, available=self.available)
        self._connect_context()
        if self.available:
            self.context_generation = 1
            functions = self._context.functions()
            self.info.update(renderer=functions.glGetString(0x1F01), vendor=functions.glGetString(0x1F00),
                version=functions.glGetString(0x1F02))
            self._context.doneCurrent()

    def _connect_context(self):
        from PySide6.QtCore import Qt
        assert self._context is not None
        self._context.aboutToBeDestroyed.connect(self._context_about_to_die, Qt.ConnectionType.DirectConnection)
        self._context.destroyed.connect(self._context_gone, Qt.ConnectionType.DirectConnection)

    def _context_about_to_die(self):
        """Direct Qt destruction callback; never propagate exceptions through Qt."""
        from PySide6.QtGui import QOpenGLContext
        self.available = False
        self._gpu_failure = "GPU context destroyed; select CPU fallback until explicit recreation"
        self.context_destructions += 1
        context = self._context
        if self._cleanup_error is not None:
            return  # identifiers may belong to an already-destroyed native generation
        previous = QOpenGLContext.currentContext()
        previous_surface = previous.surface() if previous is not None else None
        try:
            self._guard()
            if context is None:
                raise RuntimeError("destruction signal without owning context")
            if self._resources is not None or self._fbo is not None:
                if not self._make_current():
                    raise RuntimeError("cannot release resources before native context destruction")
                try:
                    self._release_resources()
                    self._release_target()
                finally:
                    context.doneCurrent()
        except Exception as error:
            # Cleanup failure is observable, not falsely reported as released.
            self._cleanup_error = f"{type(error).__name__}: {error}"
        finally:
            if previous is not None and previous != context and previous_surface is not None:
                if not previous.makeCurrent(previous_surface):
                    self._cleanup_error = "could not restore foreign context after destruction cleanup"

    def _context_gone(self, *_args):
        from PySide6.QtGui import QOpenGLContext
        previous = QOpenGLContext.currentContext()
        surface = previous.surface() if previous is not None else None
        if previous is not None:
            previous.doneCurrent()
        try:
            if self._resources is not None:
                self._resources.abandon_destroyed_context()
                self._last_resources = self._resources.snapshot()
                self._resources = None
            if self._fbo is not None:
                # Do NOT call release() using dead native context identifiers.
                self._device = self._fbo = self._extent = None
                self.abandoned_targets += 1
            self._last_cleanup_warning = self._cleanup_error or self._last_cleanup_warning
            self._cleanup_error = None
        except Exception as error:
            self._cleanup_error = f"{type(error).__name__}: {error}"
        finally:
            if previous is not None and surface is not None:
                previous.makeCurrent(surface)
        self._context = None
        self.available = False
        self._gpu_failure = "GPU context object destroyed; select CPU fallback until explicit recreation"

    def recreate_context(self):
        """Explicit native destroy/create boundary, no automatic per-frame retry."""
        from PySide6.QtGui import QOpenGLContext
        self._guard()
        if self._rendering:
            raise RuntimeError("cannot recreate context during a frame")
        if self._cleanup_error is not None and self._context is not None:
            from shiboken6 import delete
            # Full QObject teardown invalidates all native generations before
            # dropping wrappers; never issue GL deletes on ambiguous old IDs.
            delete(self._context)
        if self._cleanup_error is not None:
            raise GpuContextUnavailable("previous destruction cleanup failed: " + self._cleanup_error)
        if self._context is not None and (self._resources is not None or self._fbo is not None):
            if not self._make_current():
                raise GpuContextUnavailable("cannot release old graphics before recreation; use CPU fallback")
            try:
                self._release_resources()
                self._release_target()
            finally:
                self._context.doneCurrent()
        if self._context is None:
            self._context = QOpenGLContext()
            self._connect_context()
        self.available = False
        self._gpu_failure = "GPU context recreation failed; use CPU fallback"
        # Qt create() destroys an existing native context first (and emits
        # aboutToBeDestroyed), even if the Python QObject remains the same.
        created = self._context.create()
        if not created or not self._make_current():
            raise GpuContextUnavailable(self._gpu_failure)
        try:
            self.available = True
            self._gpu_failure = None
            self.context_generation += 1
            functions = self._context.functions()
            self.info.update(context_created=True, available=True,
                renderer=functions.glGetString(0x1F01), vendor=functions.glGetString(0x1F00),
                version=functions.glGetString(0x1F02))
        finally:
            self._context.doneCurrent()
        return self.context_generation

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
        return self._context is not None and self._context.makeCurrent(self._surface)

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

        Reuses only a still-valid context; use recreate_context after native
        destruction. Old graphics data is released first.
        """
        self._guard()
        if self._rendering:
            raise RuntimeError("cannot recover context during a frame")
        context = self._context
        if not self.available or context is None or not context.isValid() or not self._make_current():
            raise GpuContextUnavailable("context recovery unavailable; use CPU fallback")
        try:
            self._release_resources()
            self._release_target()
            self._gpu_failure = None
            self.context_recoveries += 1
        finally:
            context.doneCurrent()

    def render_or_cpu(self, extent, panels, background, *, draw=None, expected_generation=None):
        """Fallback draws current actual scene, never returns last GPU pixels."""
        self._guard()
        try:
            image, elapsed = self.render(extent, panels, background, draw=draw, expected_generation=expected_generation)
            return image, dict(backend="gpu", completed_paint_ms=elapsed, reason=None)
        except (GpuContextUnavailable, GpuOperationError) as error:
            retained = (self._extent.pixel_width*self._extent.pixel_height*8 if self._extent else 0)
            retained += self._resources.live_bytes if self._resources is not None else 0
            if retained + extent.pixel_width*extent.pixel_height*4 > extent.target_budget_bytes:
                raise MemoryError("CPU fallback plus retained GPU storage exceeds budget") from error
            image = image_target(extent, background)
            paint_scenes(image, panels)
            return image, dict(backend="cpu", completed_paint_ms=None, reason=str(error))

    def render(self, extent, panels, background, *, draw=None, expected_generation=None):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage, QOpenGLContext
        from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLPaintDevice
        from time import perf_counter
        self._guard()
        if self._rendering:
            raise RuntimeError("reentrant GPU frame rejected")
        if expected_generation is not None and expected_generation != self.context_generation:
            raise ValueError("stale GPU context generation")
        if self._gpu_failure is not None:
            raise GpuContextUnavailable(self._gpu_failure)
        if not self.available or not self._make_current():
            self._gpu_failure = "GPU context unavailable; caller must select CPU fallback"
            raise GpuContextUnavailable(self._gpu_failure)
        self._rendering = True
        frame_context = self._context
        assert frame_context is not None
        try:
            if extent != self._extent:
                # Destroy old before allocating replacement: no double-FBO peak.
                self._release_target()
                fmt = QOpenGLFramebufferObjectFormat()
                fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                fmt.setInternalTextureFormat(0x8058)
                fbo = QOpenGLFramebufferObject(QSize(extent.pixel_width, extent.pixel_height), fmt)
                if not fbo.isValid():
                    raise GpuOperationError("GPU scene target allocation failed")
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
                raise GpuOperationError("GPU scene target bind failed")
            functions = frame_context.functions()
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
            if self._resources is not None and self._resources.failure is not None:
                raise GpuOperationError(self._resources.failure)
            if (self._context is not frame_context or not self.available or self._gpu_failure is not None
                    or QOpenGLContext.currentContext() != frame_context):
                self._gpu_failure = "context destroyed or no longer current during frame; use CPU fallback"
                raise GpuContextUnavailable(self._gpu_failure)
            functions.glFinish()
            elapsed_ms = (perf_counter() - begin) * 1000
            # Qt readback can consume pending GL errors; reject before entering it.
            error = functions.glGetError()
            if error:
                raise GpuOperationError(f"OpenGL error {error} before readback")
            image = self._fbo.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(extent.dpr)
            error = functions.glGetError()
            if error:
                raise GpuOperationError(f"OpenGL error {error}")
            if image.isNull():
                raise GpuOperationError("GPU readback produced a null image")
            return image, elapsed_ms
        except GpuOperationError as error:
            self._gpu_failure = str(error)[:1024]
            try:
                from shiboken6 import isValid
                if not isValid(frame_context) or QOpenGLContext.currentContext() != frame_context:
                    raise RuntimeError("cannot clean failed GPU frame without owning current context")
                self._release_resources()
                self._release_target()
            except Exception as cleanup_error:
                self._cleanup_error = f"{type(cleanup_error).__name__}: {cleanup_error}"[:1024]
            raise
        finally:
            from shiboken6 import isValid
            self._rendering = False
            if frame_context is not None and isValid(frame_context) and QOpenGLContext.currentContext() == frame_context:
                frame_context.doneCurrent()

    def close(self):
        if self._closed:
            return
        self._guard()
        if self._rendering:
            raise RuntimeError("cannot close context during a frame")
        if self._cleanup_error is not None and self._context is not None:
            from shiboken6 import delete
            delete(self._context)  # full retirement before abandoning ambiguous old identifiers
        if self._cleanup_error is not None:
            raise RuntimeError("destruction cleanup remains unresolved: " + self._cleanup_error)
        if self.available and not self._make_current():
            raise RuntimeError("cannot release GL resources without owning context")
        context = self._context
        try:
            self._release_resources()
            self._release_target()
        finally:
            if context is not None:
                context.doneCurrent()
        # Do not claim closed if resource cleanup raised above.
        if context is not None:
            from shiboken6 import delete
            delete(context)  # owned QObject; native destruction signal is observed
        self._surface.destroy()
        self._closed = True
        self._gpu_failure = None

    def snapshot(self):
        return dict(allocations=self.allocations, releases=self.releases, closed=self._closed, available=self.available,
            scientific_resources=self._resources.snapshot() if self._resources is not None else self._last_resources,
            context_failure=self._gpu_failure, context_recoveries=self.context_recoveries,
            context_generation=self.context_generation, context_destructions=self.context_destructions,
            destruction_cleanup_error=self._cleanup_error,
            last_cleanup_warning=self._last_cleanup_warning, abandoned_targets=self.abandoned_targets,
            live_targets=int(self._fbo is not None), nominal_peak_target_bytes=self.peak_target_bytes,
            scope="Target accounting only; excludes Qt image/texture caches, source data, driver memory and comparison scratch")
