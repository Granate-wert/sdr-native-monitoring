"""Actual Qt traversal gate before scientific substitution, with strict scope."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import weakref

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainterPath, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsBlurEffect, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from scripts.app05_full_composition import PlotCommand, _state, build_plot_plan, curve_support_comparison, paint_qt_commands, plan_metadata, review_plot_commands
from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, image_target
from scripts.app05_scientific_layers import ScientificLayer, ScientificLayers


def composition_probe(root, *, native=False, review=False, persistent=False, recreate=False, fail_upload=False):
    with TemporaryDirectory(prefix="app05-full-composition-") as directory:
        output = Path(directory) / "full.json"
        options = ["--width", "2560", "--height", "1440", "--dpr", "1.5", "--theme", "light",
                   "--persistence", "visual"] if native else ["--width", "1366", "--height", "768"]
        if review:
            options += ["--component-review"]
        if persistent:
            options += ["--persistent-resources"]
        if recreate:
            options += ["--recreate-context"]
        if fail_upload:
            options += ["--fail-upload"]
        process = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_scene_gpu.py"),
            "--checkout", str(root), "--output", str(output), "--scientific", "--composition",
            "--platform", "windows" if native else "offscreen", *options], cwd=root,
            timeout=60, text=True, encoding="utf-8", capture_output=True)
        if process.returncode:
            raise AssertionError(process.stdout + process.stderr)
        return json.loads(output.read_text(encoding="utf-8"))


class PlotCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_item_clip_opacity_and_transform_are_not_flattened_or_mutated(self):
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        view.resize(180, 120)
        parent = QGraphicsRectItem(QRectF(0, 0, 30, 30))
        parent.setFlag(parent.GraphicsItemFlag.ItemClipsChildrenToShape)
        scene.addItem(parent)
        child = QGraphicsRectItem(QRectF(-5, -5, 80, 80), parent)
        child.setBrush(QColor("red"))
        child.setOpacity(.5)
        parent.setOpacity(.5)
        parent.setPos(10, 20)
        child.setTransform(QTransform.fromScale(2, 3))
        command = PlotCommand(child, 0, QRectF(0, 0, 180, 120), view)
        matrix, clip = _state(command)
        self.assertTrue(matrix.isAffine())
        self.assertFalse(clip.isEmpty())
        before = child.sceneTransform(), child.isVisible(), child.opacity(), parent.opacity()
        image = image_target(SceneExtent(180, 120), QColor("black"))
        paint_qt_commands(image, (command,))
        self.assertEqual(before, (child.sceneTransform(), child.isVisible(), child.opacity(), parent.opacity()))
        self.assertEqual(child.effectiveOpacity(), .25)
        metadata = plan_metadata((command,))
        weak = weakref.ref(command)
        del command
        self.assertIsNone(weak(), "metadata must not retain drawing commands/widgets")
        self.assertFalse(metadata["retained_beyond_capture"])
        view.close()

    def test_nonrectangular_clip_is_preserved_as_path(self):
        class EllipseClip(QGraphicsRectItem):
            def shape(self):
                path = QPainterPath()
                path.addEllipse(self.rect())
                return path
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        parent = EllipseClip(QRectF(0, 0, 20, 20))
        parent.setFlag(parent.GraphicsItemFlag.ItemClipsChildrenToShape)
        scene.addItem(parent)
        child = QGraphicsRectItem(QRectF(0, 0, 20, 20), parent)
        matrix, clip = _state(PlotCommand(child, 0, QRectF(0, 0, 200, 120), view))
        inverse, valid = matrix.inverted()
        self.assertTrue(valid)
        local_clip = inverse.map(clip)
        self.assertTrue(local_clip.contains(QRectF(9, 9, 1, 1)))
        self.assertFalse(local_clip.contains(QRectF(0, 0, 1, 1)))
        view.close()

    def test_curve_support_detects_lost_peak_and_gap_not_just_pixel_count(self):
        background = QColor("black")
        reference = image_target(SceneExtent(20, 20), background)
        candidate = image_target(SceneExtent(20, 20), background)
        for x in range(3, 17):
            reference.setPixelColor(x, 10, QColor("white"))
            candidate.setPixelColor(x, 11, QColor("white"))
        near = curve_support_comparison(reference, candidate, background)
        self.assertEqual(near["reference_pixels_without_candidate_within_one"], 0)
        self.assertEqual(near["candidate_pixels_without_reference_within_one"], 0)
        self.assertEqual(near["maximum_top_delta"], 1)
        self.assertEqual(near["reference_bounds"], [3, 10, 16, 10])
        self.assertEqual(near["candidate_bounds"], [3, 11, 16, 11])
        reference.setPixelColor(8, 2, QColor("white"))
        lost = curve_support_comparison(reference, candidate, background)
        self.assertEqual(lost["reference_pixels_without_candidate_within_one"], 1)
        self.assertEqual(lost["maximum_top_delta"], 9)
        for x in range(7, 10):
            candidate.setPixelColor(x, 11, background)
        gap = curve_support_comparison(reference, candidate, background)
        self.assertEqual(gap["reference_only_columns"], 3)
        self.assertGreater(gap["reference_pixels_without_candidate_within_one"], 1)
        with self.assertRaises(ValueError):
            curve_support_comparison(reference, candidate, QColor(0, 0, 0, 0))

    def test_native_isolated_command_review_does_not_mutate_scene_or_retain_commands(self):
        target = SceneGpuTarget()
        self.addCleanup(target.close)
        if not target.available:
            self.skipTest("requires native GL context")
        scene = QGraphicsScene()
        view = QGraphicsView(scene)
        view.resize(180, 120)
        item = QGraphicsRectItem(QRectF(2, 3, 20, 30))
        item.setBrush(QColor("red"))
        scene.addItem(item)
        command = PlotCommand(item, 0, QRectF(0, 0, 180, 120), view)
        original = item.pos(), item.opacity(), item.isVisible(), item.sceneTransform()
        bundle = ScientificLayers((), 0, 1024)
        report = review_plot_commands(target, SceneExtent(180, 120), (command,), QColor("black"), bundle)
        self.assertEqual(len(report["commands"]), 1)
        self.assertTrue(report["commands"][0]["comparison"]["equal"])
        self.assertFalse(report["product_accepted"])
        self.assertEqual(original, (item.pos(), item.opacity(), item.isVisible(), item.sceneTransform()))
        weak = weakref.ref(command)
        del command
        self.assertIsNone(weak())
        with self.assertRaisesRegex(ValueError, "bound"):
            review_plot_commands(target, SceneExtent(180, 120), [None]*1025, QColor("black"), bundle)
        tight = SceneExtent(180, 120, target_budget_bytes=180*120*20)
        with self.assertRaisesRegex(MemoryError, "budget"):
            review_plot_commands(target, tight, (), QColor("black"), bundle)
        target.close()
        self.assertEqual(target.snapshot()["live_targets"], 0)
        view.close()

    def test_ambiguous_missing_and_unsupported_commands_fail_before_substitution(self):
        qt_scene = QGraphicsScene()
        view = QGraphicsView(qt_scene)
        item = QGraphicsRectItem(QRectF(0, 0, 10, 10))
        qt_scene.addItem(item)
        plot = SimpleNamespace(getAxis=lambda name: None)
        owner = SimpleNamespace(_persistence=SimpleNamespace(image_item=item), _curves={},
            sweep_coverage=SimpleNamespace(history=SimpleNamespace(curve=QGraphicsRectItem()),
                                           strip=QGraphicsRectItem(), label=QGraphicsRectItem()),
            _marker_lines={}, _marker_labels={}, _band_mask_items=[], _plot_item=plot)
        waterfall = SimpleNamespace(image_items=[], _plot_item=plot)
        layer = ScientificLayer("persistence", 0, -20., (0., 0., 20., 20.), (1., 0., 0., 1., 0., 0.), 1.)
        bundle = ScientificLayers((layer,), 0, 1024)
        panels = [(view, QRectF(0, 0, 30, 30))]
        self.assertEqual(len(build_plot_plan(owner, waterfall, panels, bundle)), 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_plot_plan(owner, waterfall, panels, ScientificLayers((layer, layer), 0, 1024))
        item.hide()
        with self.assertRaisesRegex(AssertionError, "one-to-one"):
            build_plot_plan(owner, waterfall, panels, bundle)
        item.show()
        item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations)
        with self.assertRaisesRegex(ValueError, "device mapping"):
            build_plot_plan(owner, waterfall, panels, bundle)
        item.setFlag(item.GraphicsItemFlag.ItemIgnoresTransformations, False)
        item.setGraphicsEffect(QGraphicsBlurEffect())
        with self.assertRaisesRegex(ValueError, "effect"):
            build_plot_plan(owner, waterfall, panels, bundle)
        view.close()


class ActualCompositionTests(unittest.TestCase):
    def assert_plan(self, report):
        self.assertTrue(report["full_plot_composition"])
        self.assertEqual(len(report["cases"]), 4)
        for row in report["cases"]:
            self.assertTrue(row["qt_traversal_comparison"]["equal"], row["qt_traversal_comparison"])
            commands = row["plot_plan"]["commands"]
            names = [item["scientific"] for item in commands if item["scientific"]]
            expected = [item["name"] for item in row["scientific_layers"]["layers"]]
            self.assertCountEqual(names, expected)
            self.assertLessEqual(len(commands), 1024)
            density = next(i for i, item in enumerate(commands) if item["scientific"] == "persistence")
            band = next(i for i, item in enumerate(commands) if item["type"] == "LinearRegionItem")
            curve = next(i for i, item in enumerate(commands) if item["scientific"] == "current")
            self.assertLess(density, band)
            self.assertLess(band, curve)
            self.assertTrue(any(item["role"].startswith("axis:") for item in commands))
            self.assertTrue(any(item["role"].startswith("marker:M1:") for item in commands))
            self.assertTrue(any("dBFS/bin" in (item["text"] or "") for item in commands))
            if row["label"].startswith("sweep"):
                coverage = next(i for i, item in enumerate(commands) if item["scientific"] == "coverage")
                self.assertLess(band, coverage)
                self.assertLess(coverage, curve)
            self.assertTrue(row["source_unchanged"])
            self.assertFalse(row["candidate_accepted"], "full plot drawing is not production acceptance")
            self.assertFalse(row["speed_acceptance"])
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["outside_imports"], [])
        self.assertEqual(report["post_close_reserved_bytes"], 0)
        self.assertEqual(report["target_lifetime"]["live_targets"], 0)

    def test_real_cpu_traversal_bands_markers_axes_and_layers_are_exact(self):
        root = Path(__file__).resolve().parents[2]
        report = composition_probe(root)
        self.assert_plan(report)

    def test_native_persistent_resources_match_fresh_full_scene_and_release(self):
        app = QApplication.instance() or QApplication([])
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest("requires native GL context")
        report = composition_probe(Path(__file__).resolve().parents[2], native=True, persistent=True)
        self.assert_plan(report)
        for row in report["cases"]:
            self.assertTrue(row["persistent_vs_fresh_gpu"]["equal"])
            self.assertEqual(row["gpu_resources"]["persistent"]["program_builds"], 2)
        stats = report["target_lifetime"]["scientific_resources"]
        self.assertTrue(stats["closed"])
        self.assertEqual(stats["live_bytes"], 0)
        self.assertEqual(stats["live_programs"], 0)
        self.assertEqual(stats["texture_allocations"], stats["texture_releases"])
        self.assertIsNotNone(app)

    def test_native_complete_plot_composition_qhd_dpr150(self):
        app = QApplication.instance() or QApplication([])
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest("requires native GL context")
        root = Path(__file__).resolve().parents[2]
        report = composition_probe(root, native=True, review=True)
        self.assert_plan(report)
        for row in report["cases"]:
            self.assertNotIn("gpu_declined", row)
            resources = row["gpu_resources"]
            self.assertTrue(resources["all_plot_layers"])
            self.assertFalse(resources["product_accepted"])
            self.assertGreater(resources["qt_batches"], 0)
            self.assertCountEqual([item["name"] for item in resources["scientific"]],
                                  [item["name"] for item in row["scientific_layers"]["layers"]])
            isolated = row["component_review"]
            self.assertFalse(isolated["product_accepted"])
            self.assertFalse(isolated["speed_acceptance"])
            self.assertEqual(len(isolated["commands"]), row["plot_plan"]["count"])
            curves = [item["curve_support"] for item in isolated["commands"] if "curve_support" in item]
            self.assertTrue(curves)
            for support in curves:
                self.assertGreater(support["reference_pixels"], 0)
                self.assertGreater(support["candidate_pixels"], 0)
                self.assertEqual(support["reference_only_columns"], 0)
                self.assertEqual(support["candidate_only_columns"], 0)
                self.assertEqual(support["reference_pixels_without_candidate_within_one"], 0)
                self.assertEqual(support["candidate_pixels_without_reference_within_one"], 0)
        self.assertIsNotNone(app)

    def test_native_upload_failure_current_cpu_and_recovered_full_scene(self):
        app = QApplication.instance() or QApplication([])
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest("requires native GL context")
        report = composition_probe(Path(__file__).resolve().parents[2], native=True, persistent=True, fail_upload=True)
        self.assert_plan(report)
        for row in report["cases"]:
            failure = row["upload_failure"]
            self.assertTrue(failure["cpu_fallback"]["equal"])
            self.assertTrue(failure["recovered_gpu"]["equal"])
            self.assertEqual(failure["failed"]["live_targets"], 0)
            self.assertEqual(failure["failed"]["scientific_resources"]["live_bytes"], 0)
            self.assertIsNone(failure["failed"]["destruction_cleanup_error"])
        lifetime = report["target_lifetime"]
        self.assertEqual(lifetime["context_generation"], 1)
        self.assertEqual(lifetime["context_recoveries"], 4)
        self.assertEqual(lifetime["allocations"], lifetime["releases"])
        self.assertIsNotNone(app)

    def test_native_context_destruction_current_cpu_and_recreated_full_scene(self):
        app = QApplication.instance() or QApplication([])
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest("requires native GL context")
        report = composition_probe(Path(__file__).resolve().parents[2], native=True, persistent=True, recreate=True)
        self.assert_plan(report)
        for row in report["cases"]:
            renewal = row["context_recreation"]
            self.assertEqual(renewal["new_generation"], renewal["old_generation"]+1)
            self.assertTrue(renewal["cpu_fallback"]["equal"])
            self.assertTrue(renewal["renewed_gpu"]["equal"])
            self.assertEqual(renewal["destroyed"]["live_targets"], 0)
            self.assertIsNone(renewal["destroyed"]["destruction_cleanup_error"])
        lifetime = report["target_lifetime"]
        self.assertEqual(lifetime["context_generation"], 5)
        self.assertEqual(lifetime["allocations"], lifetime["releases"]+lifetime["abandoned_targets"])
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
