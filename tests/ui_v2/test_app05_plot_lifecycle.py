"""Native Qt visibility/destroy events over the experimental plot adapter."""
import gc
import unittest
import weakref
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QOpenGLContext
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import delete, isValid

from scripts.app05_plot_lifecycle import PlotGpuLifecycle, PresentationInactive
from scripts.app05_scene_gpu_support import SceneExtent, GpuContextUnavailable
from scripts.app05_scientific_gpu import draw_scientific_gpu
from tests.ui_v2 import test_app05_gpu_resources as fixtures


class PlotLifecycleTests(unittest.TestCase):
    bundle = fixtures.PersistentGpuTests.bundle

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def new_plot(self):
        widget = QWidget()
        widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        widget.show()
        adapter = PlotGpuLifecycle(widget)
        self.addCleanup(adapter.close)
        self.addCleanup(lambda: widget.close() if isValid(widget) else None)
        if not adapter.target.available:
            self.skipTest("requires native GL context")
        return widget, adapter

    def draw(self, adapter, color="red", revision=None, extent=None):
        bundle = self.bundle(color)
        def render(device, functions, size):
            draw_scientific_gpu(device, functions, size, bundle, resources=adapter.target.scientific_resources())
        return adapter.render(extent or SceneExtent(80, 64), [], QColor("black"), draw=render,
                              expected_revision=adapter.revision if revision is None else revision)

    def test_hide_releases_storage_rejects_old_work_show_uses_new_bytes(self):
        widget, adapter = self.new_plot()
        self.assertEqual(self.draw(adapter)[0].pixelColor(10, 10), QColor("red"))
        revision = adapter.revision
        widget.hide()
        self.assertFalse(adapter.active)
        self.assertEqual(adapter.target.snapshot()["live_targets"], 0)
        self.assertEqual(adapter.target.snapshot()["scientific_resources"]["live_bytes"], 0)
        with self.assertRaises(PresentationInactive):
            self.draw(adapter)
        widget.show()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.draw(adapter, revision=revision)
        self.assertEqual(self.draw(adapter, "blue")[0].pixelColor(10, 10), QColor("blue"))
        self.assertIsNone(adapter.event_error)

    def test_native_widget_destruction_closes_target_without_retaining_widget(self):
        widget, adapter = self.new_plot()
        self.draw(adapter)
        # Remove the fixture's close lambda before explicitly deleting its QObject.
        self._cleanups.pop()
        reference = weakref.ref(widget)
        delete(widget)
        del widget
        gc.collect()
        self.assertIsNone(reference())
        self.assertTrue(adapter.closed)
        self.assertTrue(adapter.target.snapshot()["closed"])
        self.assertEqual(adapter.target.snapshot()["live_targets"], 0)

    def test_hide_cleanup_failure_is_latched_not_silently_recovered_on_show(self):
        widget, adapter = self.new_plot()
        self.draw(adapter)
        with patch.object(adapter.target, "_make_current", return_value=False):
            widget.hide()
        self.assertIsNotNone(adapter.event_error)
        self.assertEqual(adapter.target.snapshot()["live_targets"], 1)
        widget.show()
        image, status = self.draw(adapter)
        self.assertEqual(status["backend"], "cpu")
        self.assertEqual(image.pixelColor(10, 10), QColor("black"))
        with self.assertRaises(GpuContextUnavailable):
            adapter.target.recover_context()
        adapter.target.recreate_context()
        self.assertEqual(self.draw(adapter)[1]["backend"], "gpu")

    def test_hide_inside_draw_rejects_completed_pixels_and_releases_after_unwind(self):
        widget, adapter = self.new_plot()
        self.draw(adapter)
        def draw(device, functions, extent):
            widget.hide()
            self.assertTrue(adapter.target._rendering)
        with self.assertRaises(PresentationInactive):
            adapter.render(SceneExtent(80, 64), [], QColor("red"), expected_revision=adapter.revision, draw=draw)
        self.assertEqual(adapter.target.snapshot()["live_targets"], 0)
        self.assertIsNone(adapter.event_error)
        widget.show()
        self.assertEqual(self.draw(adapter, "blue")[0].pixelColor(10, 10), QColor("blue"))

    def test_destroy_inside_draw_closes_only_after_native_frame_unwinds(self):
        widget, adapter = self.new_plot()
        self.draw(adapter)
        with self.assertRaises(PresentationInactive):
            adapter.render(SceneExtent(80, 64), [], QColor("red"), expected_revision=adapter.revision,
                           draw=lambda *_: delete(widget))
        self.assertTrue(adapter.closed)
        self.assertEqual(adapter.target.snapshot()["live_targets"], 0)
        self.assertIsNone(adapter.event_error)

    def test_explicit_close_during_draw_is_rejected_without_changing_active_state(self):
        widget, adapter = self.new_plot()
        def draw(*_args):
            with self.assertRaisesRegex(RuntimeError, "during a frame"):
                adapter.close()
            self.assertTrue(adapter.active)
        _, status = adapter.render(SceneExtent(80, 64), [], QColor("red"),
                                   expected_revision=adapter.revision, draw=draw)
        self.assertEqual(status["backend"], "gpu")
        self.assertTrue(widget.isVisible())

    def test_hiding_one_plot_restores_another_targets_current_context(self):
        widget, a = self.new_plot()
        _, b = self.new_plot()
        self.draw(a)
        self.draw(b)
        self.assertTrue(b.target._make_current())
        try:
            widget.hide()
            self.assertEqual(QOpenGLContext.currentContext(), b.target._context)
            self.assertEqual(a.target.snapshot()["live_targets"], 0)
            self.assertIsNone(a.event_error)
        finally:
            b.target._context.doneCurrent()

    def test_two_panels_resize_hide_and_invalidation_do_not_share_storage(self):
        left, a = self.new_plot()
        right, b = self.new_plot()
        for index in range(32):
            extent = SceneExtent(80 + index % 3, 64, 1.5 if index % 2 else 1.)
            self.assertEqual(self.draw(a, "red", extent=extent)[0].pixelColor(10, 10), QColor("red"))
            self.assertEqual(self.draw(b, "blue")[0].pixelColor(10, 10), QColor("blue"))
            before = b.target.snapshot()
            left.hide()
            self.assertEqual(before, b.target.snapshot())
            left.show()
            a.invalidate()  # explicit source/configuration boundary; not acquisition control
        a.close()
        self.assertEqual(self.draw(b, "green")[0].pixelColor(10, 10), QColor("green"))
        b.close()
        for adapter in (a, b):
            stats = adapter.target.snapshot()
            self.assertEqual(stats["allocations"], stats["releases"])
            self.assertEqual(stats["live_targets"], 0)
            self.assertEqual(stats["scientific_resources"]["live_bytes"], 0)
        self.assertIsNotNone(right)


if __name__ == "__main__":
    unittest.main()
