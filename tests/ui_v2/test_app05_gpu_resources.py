"""Native storage lifetime/reuse, exact prototype parity, explicit fault seams."""
from dataclasses import replace
import gc
import threading
import unittest
from unittest.mock import patch
import weakref

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainterPath
from PySide6.QtWidgets import QApplication, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, compare_images, image_target, paint_scenes
from scripts.app05_scientific_gpu import draw_scientific_gpu
from scripts.app05_scientific_layers import ScientificLayer, ScientificLayers
from tests.ui_v2.test_app05_vector_gpu import line_layer


class PersistentGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.target = SceneGpuTarget()
        self.addCleanup(self.target.close)
        if not self.target.available:
            self.skipTest("requires native GL context")

    def bundle(self, color="red", shape=(8, 8)):
        image = QImage(*shape, QImage.Format.Format_RGBA8888_Premultiplied)
        image.fill(QColor(color))
        layer = ScientificLayer("persistence", 0, -20., (0., 0., 80., 64.),
            (1., 0., 0., 1., 0., 0.), 1., image=image, local_rect=(2., 2., 30., 30.))
        path = QPainterPath(QPointF(8, 40))
        path.lineTo(65, 40)
        curve = line_layer(path, color=QColor("white"))
        return ScientificLayers((layer, curve), image.sizeInBytes()+1024, 64*1024*1024)

    def render(self, bundle, *, persistent=True, extent=None, target=None):
        target = target or self.target
        extent = extent or SceneExtent(80, 64)
        def draw(device, functions, size):
            resources = target.scientific_resources() if persistent else None
            draw_scientific_gpu(device, functions, size, bundle, resources=resources)
        return target.render(extent, [], QColor("black"), draw=draw)[0]

    def test_repeated_frames_reuse_storage_but_always_upload_current_bytes(self):
        bundle = self.bundle()
        for index in range(32):
            color = QColor("red" if index % 2 == 0 else "blue")
            bundle.layers[0].image.fill(color)  # deliberately same source object/size
            candidate = self.render(bundle)
            self.assertEqual(candidate.pixelColor(10, 10), color)
            if index in (0, 1, 31):
                reference = self.render(bundle, persistent=False)
                self.assertTrue(compare_images(reference, candidate)["equal"])
        stats = self.target.snapshot()["scientific_resources"]
        self.assertEqual(stats["program_builds"], 2)
        self.assertEqual(stats["texture_allocations"], 1)
        self.assertEqual(stats["buffer_allocations"], 2)
        self.assertEqual(stats["uploads"], 32)
        self.assertEqual(stats["live_textures"], 1)
        weak = weakref.ref(bundle.layers[0].image)
        del bundle
        gc.collect()
        self.assertIsNone(weak(), "resource owner must not retain CPU images")
        self.target.close()
        stats = self.target.snapshot()["scientific_resources"]
        self.assertEqual(stats["live_bytes"], 0)
        self.assertEqual(stats["live_programs"], 0)
        self.assertEqual(stats["texture_allocations"], stats["texture_releases"])

    def test_resize_replaces_texture_without_recompiling_and_preserves_pixels(self):
        for shape, extent in (((8, 8), SceneExtent(80, 64)),
                              ((16, 9), SceneExtent(96, 80, 1.5)),
                              ((8, 8), SceneExtent(80, 64))):
            bundle = self.bundle(shape=shape)
            actual = self.render(bundle, extent=extent)
            reference = self.render(bundle, extent=extent, persistent=False)
            self.assertTrue(compare_images(reference, actual)["equal"])
        stats = self.target.snapshot()["scientific_resources"]
        self.assertEqual(stats["program_builds"], 2)
        self.assertEqual(stats["texture_allocations"], 3)
        self.assertEqual(stats["texture_releases"], 2)
        self.assertEqual(self.target.snapshot()["allocations"], 3)

    def test_omitted_layer_never_replays_retained_pixels_and_restores_new_bytes(self):
        bundle = self.bundle("red")
        self.render(bundle)
        hidden = replace(bundle, layers=(bundle.layers[1],))
        actual = self.render(hidden)
        self.assertEqual(actual.pixelColor(10, 10), QColor("black"))
        self.assertEqual(actual.pixelColor(20, 40), QColor("white"))
        bundle.layers[0].image.fill(QColor("blue"))
        actual = self.render(bundle)
        self.assertEqual(actual.pixelColor(10, 10), QColor("blue"))
        stats = self.target.snapshot()["scientific_resources"]
        self.assertEqual(stats["texture_allocations"], 1)
        self.assertEqual(stats["uploads"], 2)

    def test_context_failure_fallback_draws_new_scene_and_does_not_retry_each_frame(self):
        self.render(self.bundle())
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        view.resize(80, 64)
        item = QGraphicsRectItem(QRectF(0, 0, 20, 20))
        scene.addItem(item)
        panels = [(view, QRectF(0, 0, 80, 64))]
        extent = SceneExtent(80, 64)
        with patch.object(self.target, "_make_current", return_value=False) as failed:
            for color in ("red", "blue"):
                item.setBrush(QColor(color))
                reference = image_target(extent, QColor("black"))
                paint_scenes(reference, panels)
                actual, info = self.target.render_or_cpu(extent, panels, QColor("black"))
                self.assertEqual(info["backend"], "cpu")
                self.assertIn("unavailable", info["reason"])
                self.assertTrue(compare_images(reference, actual)["equal"])
            self.assertEqual(failed.call_count, 1)
        self.target.recover_context()
        self.assertEqual(self.target.snapshot()["scientific_resources"]["live_bytes"], 0)
        self.assertEqual(self.target.snapshot()["live_targets"], 0)
        actual = self.render(self.bundle("green"))
        self.assertEqual(actual.pixelColor(10, 10), QColor("green"))
        self.assertEqual(self.target.snapshot()["context_recoveries"], 1)
        view.close()

    def test_close_failure_is_retryable_and_does_not_falsely_report_released(self):
        self.render(self.bundle())
        with patch.object(self.target._context, "makeCurrent", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "cannot release"):
                self.target.close()
        self.assertFalse(self.target.snapshot()["closed"])
        self.assertGreater(self.target.snapshot()["scientific_resources"]["live_bytes"], 0)
        self.target.close()
        self.target.close()
        self.assertEqual(self.target.snapshot()["scientific_resources"]["live_bytes"], 0)

    def test_context_thread_and_budget_guards_and_validation_are_not_hidden_by_fallback(self):
        self.render(self.bundle())
        with self.assertRaisesRegex(RuntimeError, "current context"):
            self.target.scientific_resources()
        errors = []
        def outside():
            try:
                self.target.scientific_resources()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=outside)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        def rejected(device, functions, extent):
            resources = self.target.scientific_resources()
            before = resources.snapshot()
            with self.assertRaises(MemoryError):
                resources.set_budget(resources.live_bytes-1)
            with self.assertRaisesRegex(ValueError, "slot"):
                resources.texture("unbounded-slot", self.bundle().layers[0].image)
            with self.assertRaisesRegex(ValueError, "RGBA8"):
                resources.texture("persistence", QImage(8, 8, QImage.Format.Format_RGB888))
            self.assertEqual(before, resources.snapshot())
            raise ValueError("invalid scientific data")
        with self.assertRaisesRegex(ValueError, "invalid scientific"):
            self.target.render_or_cpu(SceneExtent(80, 64), [], QColor("black"), draw=rejected)

    def test_two_targets_never_share_context_storage_or_source_content(self):
        other = SceneGpuTarget()
        self.addCleanup(other.close)
        first = self.render(self.bundle("red"))
        second = self.render(self.bundle("blue"), target=other)
        self.assertEqual(first.pixelColor(10, 10), QColor("red"))
        self.assertEqual(second.pixelColor(10, 10), QColor("blue"))
        def wrong(device, functions, extent):
            with self.assertRaisesRegex(RuntimeError, "this target"):
                self.target.scientific_resources()
        other.render(SceneExtent(80, 64), [], QColor("black"), draw=wrong)
        self.target.close()
        self.assertGreater(other.snapshot()["scientific_resources"]["live_bytes"], 0)
        actual = self.render(self.bundle("green"), target=other)
        self.assertEqual(actual.pixelColor(10, 10), QColor("green"))

    def test_texture_size_change_with_tight_budget_rejects_before_replacement_allocation(self):
        bundle = self.bundle()
        self.render(bundle)
        def constrained(device, functions, extent):
            resources = self.target.scientific_resources()
            before = resources.snapshot()
            resources.set_budget(resources.live_bytes)
            larger = self.bundle(shape=(256, 256)).layers[0].image
            with self.assertRaises(MemoryError):
                resources.texture("persistence", larger)
            after = resources.snapshot()
            self.assertEqual(after["texture_allocations"], before["texture_allocations"])
            self.assertEqual(after["live_textures"], 0)
        self.target.render(SceneExtent(80, 64), [], QColor("black"), draw=constrained)
        actual = self.render(replace(bundle, retained_bytes=bundle.retained_bytes))
        self.assertEqual(actual.pixelColor(10, 10), QColor("red"))


if __name__ == "__main__":
    unittest.main()
