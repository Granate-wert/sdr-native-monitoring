"""Actual Scene/ImageItem admission on the existing projection owner."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum import projection
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.persistence_projection import prepare_persistence_image
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_app05_persistence_worker import view
from tests.ui_v2.test_app05_viewport_projection import DisplayFrame, ManualWorker


class PersistenceWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.worker = ManualWorker()
        self.budget = PresentationAllocationBudget(8 * 1024 * 1024)
        self.port = projection.SpectrumProjector(self.worker.submit, allocation_budget=self.budget)
        self.scene = SpectrumScene()
        self.scene.plot_item.getAxis("left").setWidth(80)
        self.scene.set_projection_port(self.port)
        self.scene.resize(1100, 600)
        self.scene.show()
        self.pump()
        self.spectrum()
        self.drain()

    def tearDown(self):
        self.port.dispose()
        self.scene.clear_measurement()
        self.drain()
        self.scene.close()
        self.scene.deleteLater()
        self.pump()
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)

    def pump(self):
        for _ in range(8):
            self.app.processEvents()

    def drain(self):
        for _ in range(20):
            self.pump()
            if not self.worker.jobs:
                return
            self.worker.finish()
        self.fail("worker/GUI did not settle")

    def spectrum(self, value=-70):
        x, y = np.linspace(100e6, 101e6, 16), np.full(16, value, dtype=np.float32)
        x.setflags(write=False)
        y.setflags(write=False)
        frame = DisplayFrame(x, y, "dBm")
        self.scene.set_frame(frame, prepared=PreparedSpectrumFrame(frame))
        return frame

    def density(self, value=.2, mode=DensityValueMode.PROBABILITY, width=16):
        source = view(np.full((4, width), value, dtype=np.float32), mode)
        self.scene.set_persistence_frame(source.source_frame)
        self.scene.commit_projection()
        return source

    def test_scene_maps_only_on_worker_and_accepts_exact_readonly_image(self):
        gui = threading.get_ident()
        threads = []

        def prepare(request, **kwargs):
            threads.append(threading.get_ident())
            return prepare_persistence_image(request, **kwargs)

        with patch.object(self.scene._persistence, "_render_image", side_effect=AssertionError("GUI mapping")), \
             patch.object(projection, "prepare_persistence_image", side_effect=prepare):
            self.density(12, DensityValueMode.COUNT)
            request = self.port._active.persistence
            self.assertIsNotNone(request)
            self.assertIsNone(self.scene._persistence.image_item.image)
            expected = prepare_persistence_image(request)
            self.drain()
            np.testing.assert_array_equal(self.scene._persistence.image_item.image, expected.image)
            self.assertFalse(self.scene._persistence.image_item.image.flags.writeable)
            self.assertEqual(len(threads), 1)
            self.assertNotIn(gui, threads)
            self.assertEqual(expected.quantitative_labels, ("0 count", "12 count"))

    def test_trace_refresh_does_not_cancel_density_and_pending_does_not_smooth_twice(self):
        overlay = self.scene._persistence
        self.scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        self.density(.2)
        active = self.port._active
        expected = prepare_persistence_image(active.persistence)
        for number in range(5):
            self.spectrum(-50 + number)
            self.scene.commit_projection()
        self.assertFalse(self.port._cancel.is_set())
        self.assertIs(self.port._pending.persistence, active.persistence)
        self.drain()
        self.assertEqual(overlay.metrics.image_uploads, 1)
        np.testing.assert_array_equal(overlay._worker_history.image, expected.image)
        old = overlay._worker_history.image
        overlay._last_upload_ns = 0
        self.density(.8)
        expected = prepare_persistence_image(self.port._active.persistence)
        self.drain()
        self.assertEqual(overlay.metrics.image_uploads, 2)
        np.testing.assert_array_equal(overlay._worker_history.image, expected.image)
        self.assertFalse(np.shares_memory(old, overlay._worker_history.image))

    def test_policy_clear_hide_and_geometry_reject_late_completed_callbacks(self):
        overlay = self.scene._persistence
        self.scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        for action in (lambda: self.scene.set_persistence_logarithmic(False),
                       self.scene.clear_persistence_display,
                       lambda: self.scene.set_presentation_active(False),
                       lambda: self.scene.set_persistence_visible(False)):
            overlay._last_upload_ns = 0
            self.density(.3)
            self.worker.finish()  # completed, but not acknowledged on GUI
            uploads = overlay.metrics.image_uploads
            action()
            self.drain()
            if not overlay._visible or not overlay._presentation_active or overlay.latest_view is None:
                self.assertFalse(overlay.image_item.isVisible())
                if not overlay._presentation_active or overlay.latest_view is None:
                    self.assertIsNone(overlay.image_item.image)
                self.assertEqual(overlay.metrics.image_uploads, uploads)
            else:
                self.assertEqual(overlay.metrics.image_uploads, uploads + 1)
                self.assertFalse(overlay._worker_history.policy.logarithmic)
            self.scene.set_presentation_active(True)
            self.scene.set_persistence_visible(True)
            self.drain()
        overlay._last_upload_ns = 0
        first = self.density(.5)
        self.worker.finish()
        shifted = replace(first.source_frame, frequency_edges_hz=first.frequency_edges_hz + 2e6)
        shifted.frequency_edges_hz.setflags(write=False)
        self.scene.set_persistence_frame(shifted)
        self.scene.commit_projection()
        self.drain()
        self.assertEqual(overlay._worker_history.physical_rect[0], 102e6)

    def test_stopped_show_restores_latest_without_gui_mapping_or_new_source(self):
        self.scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        self.density(.4)
        self.drain()
        overlay = self.scene._persistence
        history = overlay._worker_history
        self.scene.set_presentation_active(False)
        self.assertIsNone(overlay.image_item.image)
        self.assertIs(overlay._worker_history, history)
        with patch.object(overlay, "_render_image", side_effect=AssertionError("GUI restore mapping")):
            self.scene.set_presentation_active(True)
            self.drain()
        self.assertIsNotNone(overlay.image_item.image)
        self.assertIsNot(overlay._worker_history, history)
        self.assertEqual(overlay.metrics.image_uploads, 2)

    def test_worker_wait_is_inside_existing_start_to_start_density_cadence(self):
        overlay = self.scene._persistence
        first = view(np.full((4, 16), .2, np.float32))
        overlay.set_frame(first, now_ns=1_000_000_000)
        self.scene.commit_projection()
        self.worker.finish()
        with patch("sdr_monitor.ui.v2.spectrum.persistence_overlay.monotonic_ns", return_value=1_020_000_000):
            self.pump()
        self.assertEqual(overlay._last_upload_ns, 1_000_000_000)
        second = view(np.full((4, 16), .7, np.float32))
        overlay.set_frame(second, now_ns=1_068_000_000)
        self.assertIsNotNone(overlay.worker_request)
        self.assertIs(overlay.worker_request.view, second)
        self.scene.commit_projection()
        self.drain()

    def test_density_denial_keeps_spectrum_and_explicit_visibility_recovery(self):
        # Retain another owner's array: sources/traces fit, image+scratch do not.
        other = np.empty(8 * 1024 * 1024 - 40000, dtype=np.uint8)
        self.budget.observe(other)
        self.density(.4, width=1024)
        self.drain()
        overlay = self.scene._persistence
        self.assertTrue(overlay.allocation_limited)
        self.assertIsNotNone(self.scene.displayed_frame)
        self.assertIsNone(overlay.image_item.image)
        denials = overlay.metrics.allocation_denials
        self.scene.set_persistence_visible(True)
        self.drain()
        self.assertEqual(overlay.metrics.allocation_denials, denials)
        del other
        self.scene.set_persistence_visible(False)
        self.scene.set_persistence_visible(True)
        self.drain()
        self.assertFalse(overlay.allocation_limited)
        self.assertIsNotNone(overlay.image_item.image)

    def test_close_dispose_does_not_apply_completed_late_image(self):
        self.density()
        self.worker.finish()
        self.port.dispose()
        self.scene.clear_measurement()
        self.drain()
        self.assertIsNone(self.scene._persistence.image_item.image)
        self.assertIsNone(self.scene._persistence._worker_history)
        self.assertEqual(self.scene._persistence.metrics.image_uploads, 0)


class PersistenceCompositionTests(unittest.TestCase):
    def setUp(self):
        from tests import test_app02_analyzer_workspace_product as product
        self.f = product.AnalyzerWorkspaceProductTests("runTest")
        self.f.app = QApplication.instance() or QApplication([])
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.f.select_and_apply()

    def test_stopped_rtbw_policy_change_while_existing_worker_is_mapping(self):
        from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
        from tests.ui_v2.test_app05_prepared_live import measurement
        f = self.f
        scene = f.page.visualization.spectrum_scene
        scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        source = measurement(f)
        source = replace(source, persistence=synthetic_persistence(source.spectrum, 32, 1, 1))
        entered, release = threading.Event(), threading.Event()
        threads, policies = [], []

        def prepare(request, **kwargs):
            threads.append(threading.get_ident())
            policies.append(request.policy)
            if len(threads) == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("density mapping barrier")
            return prepare_persistence_image(request, **kwargs)

        with patch.object(projection, "prepare_persistence_image", side_effect=prepare), \
             patch.object(scene._persistence, "_render_image", side_effect=AssertionError("GUI mapping")):
            try:
                f.presenter._emit_snapshot(source)
                f.wait(entered.is_set)
                scene.set_persistence_logarithmic(False)
                self.assertEqual(scene.persistence_metrics.image_uploads, 0)
                release.set()
                f.wait(lambda: scene.persistence_metrics.image_uploads == 1
                       and f.composition.spectrum_projector._future is None)
                history = scene._persistence._worker_history
                self.assertFalse(history.policy.logarithmic)
                self.assertNotEqual(policies[0], history.policy)
                self.assertEqual(history.revision, 1)  # cancelled image was never history
                f.shell.select_workspace("calibration")
                f.shell.select_workspace("analyzer")
                f.wait(lambda: scene.persistence_metrics.image_uploads == 2)
            finally:
                release.set()
        self.assertEqual(len(set(threads)), 1)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertEqual(f.events, [])  # stopped restoration never starts a receiver

    def test_compiled_sweep_statistics_use_worker_and_stop_keeps_latest_then_mode_clears(self):
        import importlib
        from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
        from sdr_monitor.services.native_continuous_sweep import _to_domain_progress, _to_domain_line
        from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
        from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
        partial, final = importlib.import_module("sdr_monitor._sdr_native")._make_test_sweep_statistics_frames()
        partial, final = _to_domain_progress(partial), _to_domain_line(final)
        f = self.f
        scene = f.page.visualization.spectrum_scene
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
        scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        threads = []

        def prepare(request, **kwargs):
            threads.append(threading.get_ident())
            return prepare_persistence_image(request, **kwargs)

        source = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), partial)
        with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=source) as poll, \
             patch.object(projection, "prepare_persistence_image", side_effect=prepare), \
             patch.object(scene._persistence, "_render_image", side_effect=AssertionError("GUI mapping")):
            f.page.primary.click()
            f.wait(lambda: scene.persistence_metrics.image_uploads > 0)
            self.assertIs(scene._persistence._uploaded_density, partial.statistics.probability)
            poll.return_value = ContinuousSweepDisplaySnapshot(final, ContinuousSweepDisplayMetrics())
            f.composition.analyzer_presenter._poll()
            f.wait(lambda: scene._persistence._uploaded_density is final.statistics.probability)
            f.page.primary.click()
            f.wait(lambda: f.composition.analyzer_presenter.can_close()
                   and f.composition.spectrum_projector._future is None)
            self.assertIs(scene._persistence._uploaded_density, final.statistics.probability)
            self.assertIsNotNone(scene._persistence._worker_history)
        self.assertEqual(len(set(threads)), 1)
        self.assertNotIn(threading.get_ident(), threads)
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.RTBW))
        self.assertIsNone(scene._persistence.image_item.image)
        self.assertIsNone(scene._persistence._worker_history)
        self.assertEqual(f.events, ["sweep-start", "sweep-stop"])


if __name__ == "__main__":
    unittest.main()
