"""Aggregate admission and actual native retained storage accounting."""
import threading
import unittest
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtGui import QColor
from PySide6.QtOpenGL import QOpenGLTexture
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from scripts.app05_graphics_budget import GraphicsBudget
from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, image_target
from tests.ui_v2 import test_app05_gpu_resources as fixtures


class GraphicsBudgetTests(unittest.TestCase):
    def test_transaction_denial_retention_and_nested_owners(self):
        pool = GraphicsBudget(100)
        a, b = pool.register(), pool.register()
        with pool.frame(a, 60):
            pool.retain(a, 20)
            with self.assertRaises(MemoryError):
                with pool.frame(b, 50):
                    self.fail("unadmitted frame ran")
            self.assertEqual(pool.charged_bytes, 60)
        self.assertEqual(pool.charged_bytes, 20)
        with pool.frame(b, 80):
            pool.retain(b, 30)
        self.assertEqual(pool.charged_bytes, 50)
        with pool.frame(a, 10):
            self.assertEqual(pool.charged_bytes, 50)
            pool.retain(a, 0)
        pool.unregister(a)
        pool.retain(b, 0)
        pool.unregister(b)
        self.assertEqual(pool.snapshot()["owners"], 0)
        self.assertEqual(pool.snapshot()["peak_bytes"], 100)

    def test_bounds_thread_and_active_owner_guards(self):
        for value in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                GraphicsBudget(value)
        pool = GraphicsBudget(100)
        owners = [pool.register() for _ in range(64)]
        with self.assertRaises(MemoryError):
            pool.register()
        errors = []
        def wrong_thread():
            try:
                pool.register()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(2)
        self.assertEqual(len(errors), 1)
        with pool.frame(owners[0], 100):
            with self.assertRaises(RuntimeError):
                pool.unregister(owners[0])
            with self.assertRaises(RuntimeError):
                pool.retain(owners[0], 101)
        for owner in owners:
            pool.unregister(owner)


class NativeGraphicsBudgetTests(unittest.TestCase):
    bundle = fixtures.PersistentGpuTests.bundle
    render = fixtures.PersistentGpuTests.render

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pool = GraphicsBudget(300*1024)
        self.target = SceneGpuTarget(graphics_budget=self.pool)
        self.other = SceneGpuTarget(graphics_budget=self.pool)
        self.addCleanup(self.target.close)
        self.addCleanup(self.other.close)
        if not self.target.available or not self.other.available:
            self.skipTest("requires native GL context")
        self.extent = SceneExtent(80, 64, target_budget_bytes=256*1024)

    def test_second_frame_charges_first_retention_and_denies_before_mutation(self):
        self.render(self.bundle(), extent=self.extent)
        first = self.pool.charged_bytes
        self.assertGreater(first, 80*64*8)
        self.assertEqual(self.render(self.bundle("blue"), extent=self.extent, target=self.other).pixelColor(10, 10), QColor("blue"))
        self.assertEqual(self.pool.charged_bytes, first*2)
        snapshots = self.target.snapshot(), self.other.snapshot()
        with self.assertRaises(MemoryError):
            self.render(self.bundle(), extent=SceneExtent(80, 64, target_budget_bytes=300*1024))
        self.assertEqual(self.target.allocations, snapshots[0]["allocations"])
        self.assertEqual(self.other.allocations, snapshots[1]["allocations"])
        self.assertIsNone(self.target.snapshot()["context_failure"])
        self.other.close()
        self.render(self.bundle("green"), extent=SceneExtent(80, 64, target_budget_bytes=300*1024))
        self.target.close()
        self.assertEqual(self.pool.charged_bytes, 0)
        self.assertEqual(self.pool.snapshot()["owners"], 0)

    def test_native_destruction_abandon_releases_charge_and_recreation_recharges(self):
        self.render(self.bundle(), extent=self.extent)
        delete(self.target._context)
        self.assertEqual(self.pool.charged_bytes, 0)
        self.target.recreate_context()
        self.render(self.bundle(), extent=self.extent)
        self.assertGreater(self.pool.charged_bytes, 0)
        self.target.discard_storage()
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_cached_resource_reference_cannot_allocate_outside_frame(self):
        self.render(self.bundle(), extent=self.extent)
        resource = self.target._resources
        self.target._make_current()
        try:
            with self.assertRaisesRegex(RuntimeError, "admitted render"):
                resource.write("image", b"1234")
        finally:
            self.target._context.doneCurrent()

    def test_failed_cleanup_keeps_charge_until_explicit_retirement(self):
        self.render(self.bundle(), extent=self.extent)
        charged = self.pool.charged_bytes
        with patch.object(self.target, "_make_current", return_value=False):
            with self.assertRaises(RuntimeError):
                self.target.discard_storage()
        self.assertEqual(charged, self.pool.charged_bytes)
        self.target.recreate_context()
        self.assertEqual(self.pool.charged_bytes, 0)

    def test_cpu_fallback_has_aggregate_admission_too(self):
        self.render(self.bundle(), extent=self.extent, target=self.other)
        observed = []
        def cpu_image(*args):
            observed.append(self.pool.snapshot())
            return image_target(*args)
        with patch.object(self.target, "_make_current", return_value=False), \
             patch("scripts.app05_scene_gpu_support.image_target", side_effect=cpu_image):
            image, status = self.target.render_or_cpu(self.extent, [], QColor("green"))
        self.assertEqual(status["backend"], "cpu")
        self.assertEqual(image.pixelColor(10, 10), QColor("green"))
        self.assertGreater(self.pool.snapshot()["peak_bytes"], self.extent.target_budget_bytes)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["active_frames"], 1)
        self.assertGreater(observed[0]["charged_bytes"], self.extent.target_budget_bytes)
        self.assertEqual(self.pool.snapshot()["active_frames"], 0)

    def test_upload_fault_releases_shared_charge_and_does_not_latch_budget(self):
        from scripts.app05_scientific_gpu import draw_scientific_gpu
        bundle = self.bundle()
        def draw(device, functions, extent):
            draw_scientific_gpu(device, functions, extent, bundle, resources=self.target.scientific_resources())
        with patch.object(QOpenGLTexture, "setData", side_effect=RuntimeError("injected upload")):
            _, status = self.target.render_or_cpu(self.extent, [], QColor("blue"), draw=draw)
        self.assertEqual(status["backend"], "cpu")
        self.assertEqual(self.pool.charged_bytes, 0)
        self.assertEqual(self.pool.snapshot()["active_frames"], 0)
        self.target.recover_context()
        self.render(bundle, extent=self.extent)
        self.assertGreater(self.pool.charged_bytes, 0)


class ActualMultiCanvasBudgetTests(unittest.TestCase):
    def test_actual_rtbw_and_sweep_independent_admission_hide_and_close(self):
        app = QApplication.instance() or QApplication([])
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest("requires native GL context")
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-multi-canvas-") as directory:
            output = Path(directory) / "result.json"
            run = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_multi_canvas_gpu.py"),
                                  "--checkout", str(root), "--output", str(output)], cwd=root,
                                 timeout=60, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(report["cases"]), 13)
        self.assertEqual(report["graphics_budget"]["denials"], 1)
        self.assertEqual(report["graphics_budget"]["peak_bytes"], 80*1024*1024)
        self.assertEqual(report["graphics_budget"]["charged_bytes"], 0)
        self.assertEqual(report["graphics_budget"]["owners"], 0)
        self.assertEqual(report["product_reserved"], [0, 0])
        self.assertEqual(report["workers"], [])
        self.assertEqual(report["outside_imports"], [])
        for row in report["cases"]:
            self.assertTrue(row["source_unchanged"])
            self.assertTrue(row["qt_traversal_exact"])
            self.assertFalse(row["product_accepted"])
            self.assertFalse(row["speed_accepted"])
        for stats in report["targets"]:
            self.assertEqual(stats["allocations"], stats["releases"] + stats["abandoned_targets"])
            self.assertEqual(stats["live_targets"], 0)
            self.assertTrue(stats["closed"])
        self.assertIsNotNone(app)
