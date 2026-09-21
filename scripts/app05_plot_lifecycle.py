"""Experimental widget lifetime adapter; never imported by product code.

No payload, command plan, image cache, timer, queue or acquisition ownership.
Callers render current scene synchronously with a checked presentation revision.
Stop/measurement identity remain governed by existing product presenters.
"""
import weakref

from PySide6.QtCore import QEvent, QObject
from shiboken6 import isValid

from scripts.app05_scene_gpu_support import SceneGpuTarget


class PresentationInactive(RuntimeError):
    pass


class PlotGpuLifecycle(QObject):
    def __init__(self, widget):
        super().__init__()
        self._widget = weakref.ref(widget)
        self.target = SceneGpuTarget()
        self.revision = 0
        self.closed = False
        self.event_error = None
        self.active = widget.isVisible()
        widget.installEventFilter(self)
        widget.destroyed.connect(self._destroyed)

    def eventFilter(self, watched, event):
        try:
            if event.type() == QEvent.Type.Hide:
                self.active = False
                self.invalidate()
            elif event.type() == QEvent.Type.Show:
                self.revision += 1
                self.active = True
        except Exception as error:
            # Never propagate through Qt. Failed cleanup is explicit and latched.
            self.event_error = f"{type(error).__name__}: {error}"[:1024]
        return False

    def invalidate(self):
        self.target._guard()
        self.revision += 1
        if not self.target._rendering:
            self.target.discard_storage()

    def render(self, extent, panels, background, *, expected_revision, draw=None):
        self.target._guard()
        widget = self._widget()
        if self.closed or widget is None or not isValid(widget) or not self.active or not widget.isVisible():
            raise PresentationInactive("hidden or destroyed plot cannot render/upload")
        if expected_revision != self.revision:
            raise ValueError("stale plot presentation revision")
        try:
            result = self.target.render_or_cpu(extent, panels, background, draw=draw)
        finally:
            # A synchronous callback can hide/destroy the widget inside draw.
            # Defer resource deletion until the GL frame has unwound, not a timer.
            if not isValid(widget):
                self.target.close()
                self.closed = True
            elif self.revision != expected_revision:
                self.target.discard_storage()
        if self.revision != expected_revision or not self.active:
            raise PresentationInactive("plot changed during frame; result rejected")
        return result

    def _destroyed(self, *_args):
        self.active = False
        self.revision += 1
        if self.target._rendering:
            return
        try:
            self.target.close()
            self.closed = True
        except Exception as error:
            self.event_error = f"{type(error).__name__}: {error}"[:1024]

    def close(self):
        if self.closed:
            return
        self.target._guard()
        if self.target._rendering:
            raise RuntimeError("cannot close plot adapter during a frame")
        self.active = False
        self.target.close()
        widget = self._widget()
        if widget is not None and isValid(widget):
            widget.removeEventFilter(self)
            widget.destroyed.disconnect(self._destroyed)
        self.closed = True
