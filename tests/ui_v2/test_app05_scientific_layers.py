"""Explicit experimental layer geometry, ownership, finite paths and native shader."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainterPath
from PySide6.QtWidgets import QApplication

from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, compare_images, image_target
from scripts.app05_scientific_gpu import draw_scientific_gpu, physical_scissor, texture_vertices
from scripts.app05_scientific_layers import ScientificLayer, ScientificLayers, detach_layers, paint_layers, paint_texel_oracle


class ScientificGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self):
        view = pg.GraphicsLayoutWidget()
        view.resize(500, 400)
        plot = view.addPlot()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        view.show()
        self.app.processEvents()
        plot.setXRange(100e6, 104e6, padding=0)
        plot.setYRange(-100, 0, padding=0)
        item = pg.ImageItem(axisOrder="row-major")
        pixels = np.array([[[255, 0, 0, 255], [0, 255, 0, 255]],
                           [[0, 0, 255, 255], [0, 0, 0, 0]]], dtype=np.uint8)
        item.setImage(pixels, autoLevels=False)
        item.setRect(QRectF(101e6, -20, 2e6, -60))
        item.render()
        plot.addItem(item)
        item.setZValue(-20)
        curve = pg.PlotDataItem(np.arange(5) + 100e6, [-90, -80, np.nan, -40, -30], connect="finite")
        plot.addItem(curve)
        self.app.processEvents()
        history = pg.PlotDataItem()
        history.hide()
        plot.addItem(history)
        scene = SimpleNamespace(_persistence=SimpleNamespace(image_item=item),
            _curves={SimpleNamespaceKey(): curve}, sweep_coverage=SimpleNamespace(history=history))
        waterfall = SimpleNamespace(image_items=[])
        panels = [(view, QRectF(3, 7, 480, 350))]
        self.addCleanup(view.close)
        return view, item, curve, scene, waterfall, panels

    def test_snapshot_preserves_negative_axes_and_detaches_pixels_and_paths(self):
        view, item, curve, scene, waterfall, panels = self.fixture()
        bundle = detach_layers(scene, waterfall, panels)
        image, path = bundle.layers
        source = view.mapToScene(view.viewport().rect()).boundingRect()
        target = panels[0][1]
        for point in (QPointF(0, 0), QPointF(1, 1), QPointF(2, 2)):
            actual = item.mapToScene(point)
            expected = QPointF(target.x() + (actual.x() - source.x()) * target.width() / source.width(),
                               target.y() + (actual.y() - source.y()) * target.height() / source.height())
            mapped = image.matrix().map(point)
            self.assertAlmostEqual(mapped.x(), expected.x(), places=8)
            self.assertAlmostEqual(mapped.y(), expected.y(), places=8)
        self.assertEqual(image.image.format(), QImage.Format.Format_RGBA8888_Premultiplied)
        old = bytes(image.image.constBits())
        item.qimage.fill(QColor("white"))
        item.setRect(QRectF(0, 0, 1, 1))
        self.assertEqual(bytes(image.image.constBits()), old)
        self.assertGreaterEqual(sum(path.path.elementAt(i).isMoveTo() for i in range(path.path.elementCount())), 2)
        original_count = path.path.elementCount()
        curve.setData([0, 1], [0, 1])
        self.assertEqual(path.path.elementCount(), original_count)
        self.assertEqual(image.z, -20)

    def test_budget_refuses_before_copy_and_rotation_is_not_silently_flattened(self):
        _, item, _, scene, waterfall, panels = self.fixture()
        with patch.object(QImage, "copy", side_effect=AssertionError("allocated before budget")):
            with self.assertRaises(MemoryError):
                detach_layers(scene, waterfall, panels, limit_bytes=1)
        item.setRotation(3)
        with self.assertRaisesRegex(ValueError, "axis-aligned"):
            detach_layers(scene, waterfall, panels)

    def test_scissor_and_vertices_keep_dpr_and_negative_height(self):
        extent = SceneExtent(100, 80, 1.5)
        self.assertEqual(physical_scissor((10, 20, 30, 40), extent), (15, 30, 45, 60))
        self.assertEqual(physical_scissor((-10, -10, 5, 5), extent), (0, 120, 0, 0))
        layer = ScientificLayer("negative", 0, -20, (0, 0, 100, 80),
            (2., 0., 0., -3., 20., 60.), 1., local_rect=(0, 0, 5, 10))
        self.assertEqual(layer.target_rect(), (20, 60, 10, -30))
        np.testing.assert_array_equal(texture_vertices(), [[-1, 1], [1, 1], [-1, -1], [1, -1]])


class SimpleNamespaceKey:
    value = "current"


class ScientificNativeShaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_shader_known_texels_orientation_alpha_clip_and_resize(self):
        target = SceneGpuTarget()
        self.addCleanup(target.close)
        if not target.available:
            self.skipTest("native GL context unavailable; run this test with QT_QPA_PLATFORM=windows")
        pixels = QImage(2, 2, QImage.Format.Format_RGBA8888_Premultiplied)
        for x, y, color in ((0, 0, "red"), (1, 0, "green"), (0, 1, "blue"), (1, 1, "transparent")):
            pixels.setPixelColor(x, y, QColor(color))
        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(1, 1)
        for dpr in (1., 1.25, 1.5, 2.):
            for direction in (1., -1.):
                with self.subTest(dpr=dpr, direction=direction):
                    extent = SceneExtent(80, 64, dpr)
                    layer = ScientificLayer("known", 0, -20, (12., 12., 28., 24.),
                        (16., 0., 0., 12. * direction, 8., 8. if direction > 0 else 32.), 1.,
                        image=pixels, local_rect=(0., 0., 2., 2.))
                    bundle = ScientificLayers((layer,), 16, 1024)
                    reference = image_target(extent, QColor("black"))
                    paint_layers(reference, bundle)
                    candidate, _ = target.render(extent, [], QColor("black"),
                        draw=lambda device, functions, size: draw_scientific_gpu(device, functions, size, bundle))
                    self.assertTrue(compare_images(reference, candidate)["equal"], compare_images(reference, candidate))
                    # Independent known texel check, not merely two shared wrong renderers.
                    self.assertEqual(candidate.pixelColor(int(16*dpr), int(16*dpr)),
                                     QColor("red" if direction > 0 else "blue"))
                    self.assertEqual(candidate.pixelColor(int(32*dpr), int(28*dpr)),
                                     QColor("black" if direction > 0 else "green"))
                    self.assertEqual(candidate.pixelColor(int(10*dpr), int(16*dpr)), QColor("black"))
                    # Fractional half-pixel edges and alpha are checked against
                    # independently sampled image data, not Qt's raster rounding.
                    fractional = replace(layer, clip=(0., 0., 80., 64.),
                        transform=(16., 0., 0., -12., 8.000000000000002, 32.00000000000001))
                    pixels.setPixelColor(1, 1, QColor(173, 91, 45, 91))
                    exact_bundle = ScientificLayers((fractional,), 16, 1024)
                    oracle = image_target(extent, QColor("#39424a")).convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
                    paint_texel_oracle(oracle, exact_bundle)
                    exact, _ = target.render(extent, [], QColor("#39424a"),
                        draw=lambda device, functions, size: draw_scientific_gpu(device, functions, size, exact_bundle))
                    self.assertTrue(compare_images(oracle.convertToFormat(exact.format()), exact)["equal"])
                    pixels.setPixelColor(1, 1, QColor("transparent"))
                    with self.assertRaisesRegex(MemoryError, "combined"):
                        draw_scientific_gpu(None, None, extent, replace(bundle, retained_bytes=512*1024*1024))
        target.close()
        self.assertEqual(target.snapshot()["allocations"], 4)
        self.assertEqual(target.snapshot()["releases"], 4)
        self.assertEqual(target.snapshot()["live_targets"], 0)

    def test_actual_qhd_visual_images_match_float64_texel_oracle(self):
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest("native GL context unavailable")
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-native-texels-") as directory:
            output = Path(directory) / "scene.json"
            run = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_scene_gpu.py"),
                "--checkout", str(root), "--output", str(output), "--platform", "windows",
                "--width", "2560", "--height", "1440", "--dpr", "1.5", "--theme", "light",
                "--persistence", "visual", "--scientific", "--images-only"], cwd=root, timeout=40,
                text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        for row in report["cases"]:
            self.assertTrue(row["analytic_image_comparison"]["equal"], row["analytic_image_comparison"])
            self.assertFalse(row["candidate_accepted"], "partial images must never pass whole-renderer gate")
            self.assertFalse(row["gpu_resources"]["all_layers"])
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["post_close_reserved_bytes"], 0)


class ScientificActualCompositionTests(unittest.TestCase):
    def test_actual_source_images_paths_coverage_omissions_and_owners_are_explicit(self):
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-scientific-") as directory:
            output = Path(directory) / "scene.json"
            run = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_scene_gpu.py"),
                "--checkout", str(root), "--output", str(output), "--platform", "offscreen",
                "--width", "1366", "--height", "768", "--scientific"], cwd=root, timeout=40,
                text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(report["scientific_only"])
        self.assertEqual(len(report["cases"]), 4)
        for row in report["cases"]:
            self.assertFalse(row["candidate_accepted"])
            layers = row["scientific_layers"]
            self.assertNotIn("coverage", layers["omitted"])
            self.assertIn("markers", layers["omitted"])
            if row["label"].startswith("sweep"):
                coverage = next(layer for layer in layers["layers"] if layer["name"] == "coverage")
                self.assertGreater(len(coverage["coverage_rects"]), 0)
                self.assertLess(coverage["z"], next(layer["z"] for layer in layers["layers"] if layer["name"] == "current"))
            self.assertLess(layers["retained_bytes"], layers["limit_bytes"])
            self.assertIn("persistence", [layer["name"] for layer in layers["layers"]])
            self.assertTrue(any(layer["name"].startswith("waterfall-") for layer in layers["layers"]))
            self.assertTrue(any(layer["path_elements"] > 1 for layer in layers["layers"]))
            self.assertTrue(row["source_unchanged"])
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["outside_imports"], [])
        self.assertEqual(report["post_close_reserved_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
