"""Bounded experimental common target; normal product renderer is untouched."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication

from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, compare_images, image_target


class CommonSceneTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_extent_accounts_physical_pixels_and_all_declared_targets(self):
        extent = SceneExtent(2560, 1440, 1.5)
        self.assertEqual((extent.pixel_width, extent.pixel_height), (3840, 2160))
        self.assertEqual(extent.nominal_target_bytes, 3840 * 2160 * 20)
        self.assertEqual(SceneExtent(101, 51, 1.25).pixel_width, 127)
        for args in ((0, 100), (True, 100), (10, 10, float("nan")), (10, 10, True),
                     (9000, 4000), (1920, 1080, 1, 1024)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SceneExtent(*args)

    def test_compare_reports_real_changes_without_rounding_them_away(self):
        extent = SceneExtent(101, 51, 1.25)
        a = image_target(extent, QColor("black"))
        b = a.copy()
        self.assertTrue(compare_images(a, b)["equal"])
        b.setPixelColor(3, 7, QColor(1, 0, 0))
        b.setPixelColor(9, 11, QColor(20, 0, 0))
        report = compare_images(a, b)
        self.assertEqual(report["different_pixels"], 2)
        self.assertEqual(report["pixels_error_gt_one"], 1)
        self.assertEqual(report["pixels_error_gt_four"], 1)
        self.assertEqual(report["max_channel_error"], 20)
        self.assertEqual(report["difference_bounds"], [3, 7, 9, 11])

    def test_unavailable_gpu_has_explicit_cpu_path_and_thread_close_guards(self):
        target = SceneGpuTarget()
        errors = []
        thread = threading.Thread(target=lambda: self._capture_error(target, errors))
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIn("another thread", str(errors[0]))
        try:
            if not target.available:
                with self.assertRaisesRegex(RuntimeError, "CPU fallback"):
                    target.render(SceneExtent(32, 32), [], QColor("black"))
            image = image_target(SceneExtent(32, 32), QColor("black"))
            painter = QPainter(image)
            painter.fillRect(QRectF(2, 2, 8, 8), QColor("red"))
            painter.end()
            self.assertEqual(image.pixelColor(3, 3), QColor("red"))
        finally:
            target.close()
        target.close()
        self.assertEqual(target.snapshot()["live_targets"], 0)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            target.render(SceneExtent(32, 32), [], QColor("black"))

    @staticmethod
    def _capture_error(target, errors):
        try:
            target.render(SceneExtent(32, 32), [], QColor("black"))
        except RuntimeError as error:
            errors.append(error)


class CommonSceneCompositionTests(unittest.TestCase):
    def test_actual_rtbw_and_progressive_sweep_share_layers_and_release_owners(self):
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-common-scene-") as directory:
            output = Path(directory) / "scene.json"
            run = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_scene_gpu.py"),
                "--checkout", str(root), "--output", str(output), "--platform", "offscreen",
                "--width", "1366", "--height", "768"], cwd=root, timeout=40,
                text=True, encoding="utf-8", capture_output=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(report["same_rtbw_sweep_canvas"])
        self.assertEqual(report["events"], ["rtbw-start", "rtbw-stop", "sweep-start", "sweep-stop"])
        self.assertEqual([row["label"] for row in report["cases"]],
                         ["rtbw-live", "rtbw-stopped", "sweep-partial", "sweep-complete"])
        for row in report["cases"]:
            self.assertTrue(row["source_unchanged"])
            self.assertTrue(row["source"]["density_visible"])
            self.assertGreater(row["source"]["waterfall_visible_tiles"], 0)
            self.assertGreater(row["source"]["waterfall_rows"], 0)
            self.assertLess(row["source"]["persistence_z"], row["source"]["current_z"])
            self.assertEqual(row["source"]["persistence_mode"], "direct")
            self.assertFalse(row["speed_acceptance"])
            if row["comparison"] is None or not row["comparison"]["equal"]:
                self.assertFalse(row["candidate_accepted"])
                self.assertTrue(row["reference_selected"])
        partial, final = report["cases"][2:]
        self.assertEqual(partial["source"]["publication_kind"], "sweep_progress")
        self.assertNotEqual(partial["source"]["hashes"]["spectrum"], final["source"]["hashes"]["spectrum"])
        self.assertGreater(partial["source"]["sweep_coverage_runs"], 0)
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["outside_imports"], [])
        self.assertEqual(report["post_close_reserved_bytes"], 0)
        self.assertEqual(report["target_lifetime"]["live_targets"], 0)
        self.assertTrue(report["target_lifetime"]["closed"])


if __name__ == "__main__":
    unittest.main()
