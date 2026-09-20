"""Shared retained-array admission, actual lifetimes and control-pressure gates."""
from dataclasses import replace
import gc
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.live import LivePersistenceFrame, LiveSessionState
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget, PresentationBudgetExceeded
from sdr_monitor.ui.v2.spectrum.persistence_contracts import adapt_persistence_density
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.waterfall.bounded_ring import BoundedWaterfallRenderer
from tests.ui_v2.test_app05_retained_bytes import request
from tests.ui_v2.test_app05_viewport_projection import ManualWorker
from tests.ui_v2.test_spectrum_scene import _persistence_frame
from tests.ui_v2.test_app05_prepared_live import measurement
from tests import test_app02_analyzer_workspace_product as product


class AllocationBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_backing_lifetime_is_deduplicated_and_not_extended(self):
        budget = PresentationAllocationBudget(10000)
        root = np.arange(1000, dtype=np.uint8)
        view = root[1:2]
        budget.observe(root, view)
        budget.observe(view)
        self.assertEqual(budget.snapshot().observed_bytes, 1000)
        del root
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 1000)
        del view
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 0)

    def test_shared_byte_buffer_aliases_remain_charged_until_last_array_dies(self):
        budget = PresentationAllocationBudget(10000)
        data = bytearray(1000)
        a = np.frombuffer(data, dtype=np.uint8, count=10)
        b = np.frombuffer(data, dtype=np.uint8, count=20)
        budget.observe(a, b)
        self.assertEqual(budget.snapshot().observed_bytes, 1000)
        del a
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 1000)
        del b
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 0)

    def test_reservations_compete_atomically_and_commit_follows_output_lifetime(self):
        budget = PresentationAllocationBudget(100)
        source = np.zeros(40, dtype=np.uint8)
        ticket = budget.reserve(50, source)
        rejected = []
        def worker():
            try:
                with budget.reserve(20):
                    self.fail("second worker admitted beyond capacity")
            except PresentationBudgetExceeded:
                rejected.append(True)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(rejected, [True])
        output = np.zeros(50, dtype=np.uint8)
        ticket.commit(output)
        ticket.close()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(budget.snapshot().observed_bytes, 90)
        del output
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 40)

    def test_observed_oversize_source_is_explicit_not_falsely_capped(self):
        budget = PresentationAllocationBudget(10)
        source = np.zeros(20, dtype=np.uint8)
        with self.assertRaises(PresentationBudgetExceeded):
            budget.reserve(1, source)
        self.assertEqual(budget.snapshot().observed_bytes, 20)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(budget.snapshot().rejections, 1)

    def test_exception_and_underreservation_never_leak_a_ticket(self):
        budget = PresentationAllocationBudget(10)
        with self.assertRaisesRegex(RuntimeError, "exceeded"):
            with budget.reserve(1) as ticket:
                ticket.commit(np.zeros(2, dtype=np.uint8))
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(budget.snapshot().observed_bytes, 0)

    def test_projection_cancel_before_execution_releases_shared_reservation(self):
        worker = ManualWorker()
        budget = PresentationAllocationBudget(100000)
        port = SpectrumProjector(worker.submit, allocation_budget=budget)
        req = request()
        port.offer(req)
        self.assertGreater(budget.snapshot().reserved_bytes, 0)
        port.dispose()
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertIsNone(port._future)

    def test_projection_capacity_retry_after_release_is_once_and_has_no_payload_backlog(self):
        worker = ManualWorker()
        budget = PresentationAllocationBudget(10000)
        port = SpectrumProjector(worker.submit, allocation_budget=budget)
        req = request()
        retained = np.zeros(9000, dtype=np.uint8)
        budget.observe(retained)
        port.retry_ready.connect(lambda: port.offer(req))
        port.offer(req)
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual(budget.snapshot().rejections, 2)  # initial + one deferred attempt
        self.assertIsNone(port._pending)
        self.assertEqual(worker.jobs, [])
        # A new viewport identity gets its own bounded recovery opportunity.
        req = replace(req, generation=2)
        port.offer(req)
        del retained
        gc.collect()
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(len(worker.jobs), 1)
        worker.finish()
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        port.dispose()

    def test_waterfall_resize_reserves_both_rings_and_old_tiles_stay_charged(self):
        budget = PresentationAllocationBudget(2000)
        renderer = BoundedWaterfallRenderer()
        renderer.allocation_budget = budget
        renderer.append(np.ones(10, dtype=np.float32), rows=10, timestamp_ns=1)
        tiles = renderer.tiles()
        self.assertEqual(budget.snapshot().observed_bytes, 480)
        budget.limit_bytes = 1000
        with self.assertRaises(PresentationBudgetExceeded):
            renderer.resize_rows(20)
        self.assertEqual(renderer.buffer.rows, 10)
        self.assertEqual(renderer.buffer.count, 1)
        budget.limit_bytes = 2000
        renderer.resize_rows(20)
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 1360)  # old data tile, old timestamps released
        del tiles
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 960)
        renderer.reset()
        self.assertEqual(budget.snapshot().observed_bytes, 0)

    def test_persistence_denial_does_not_map_or_keep_old_image_and_recovers(self):
        scene = SpectrumScene()
        overlay = scene._persistence
        budget = PresentationAllocationBudget(100000)
        overlay.allocation_budget = budget
        view = adapt_persistence_density(_persistence_frame(np.full((4, 16), .25)))
        try:
            overlay.set_frame(view, now_ns=1)
            self.assertIsNotNone(overlay.image_item.image)
            budget.limit_bytes = 1
            with patch.object(overlay, "_render_image", side_effect=AssertionError("allocation attempted")):
                overlay.set_logarithmic(False)
            self.assertTrue(overlay.allocation_limited)
            self.assertIsNone(overlay.image_item.image)
            self.assertFalse(overlay._timer.isActive())
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
            budget.limit_bytes = 100000
            overlay.set_frame(view, now_ns=10**9)
            self.assertFalse(overlay.allocation_limited)
            self.assertIsNotNone(overlay.image_item.image)
            self.assertEqual(overlay.metrics.allocation_denials, 1)
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()

    def test_visual_density_reuses_charged_history_and_tracks_scratch_once(self):
        scene = SpectrumScene()
        overlay = scene._persistence
        budget = PresentationAllocationBudget(100000)
        overlay.allocation_budget = budget
        overlay.set_render_mode(PersistenceRenderMode.VISUAL)
        view = adapt_persistence_density(_persistence_frame(np.full((4, 16), .25)))
        try:
            overlay.set_frame(view, now_ns=1)
            image = overlay._visual_buffer
            view2 = adapt_persistence_density(_persistence_frame(np.full((4, 16), .75)))
            overlay.set_frame(view2, now_ns=10**9)
            self.assertIs(overlay._visual_buffer, image)
            self.assertTrue(np.shares_memory(overlay.image_item.image, image))
            self.assertEqual(overlay._row_scratch.nbytes, 64)
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
            overlay.set_presentation_active(False)
            self.assertIs(overlay._visual_buffer, image)
            self.assertIsNone(overlay.image_item.image)
            overlay.set_presentation_active(True)
            self.assertTrue(np.shares_memory(overlay.image_item.image, image))
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()


class ProductAllocationBudgetTests(unittest.TestCase):
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    wait = product.AnalyzerWorkspaceProductTests.wait
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def test_sweep_pressure_preserves_existing_history_and_terminal_stop(self):
        from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
        from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
        from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
        from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
        self.select_and_apply()
        self.page.mode.setCurrentIndex(self.page.mode.findData(AnalyzerMode.SWEEP))
        pane = self.page.visualization.waterfall_pane
        first = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress())
        with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=first) as poll:
            self.page.primary.click()
            self.wait(lambda: pane.history_rows == 1)
            second = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress(2, 2))
            budget = self.composition.allocation_budget
            # This gate exercises derived-output pressure, not source refusal.
            budget.observe(second)
            budget.limit_bytes = 1
            poll.return_value = second
            self.wait(lambda: self.composition.analyzer_view_model.state.sweep_snapshot is second)
            self.assertEqual(pane.history_rows, 1)
            self.assertTrue(self.composition.analyzer_view_model.state.running)
            final = ContinuousSweepDisplaySnapshot(terminal(2, gap=True), ContinuousSweepDisplayMetrics(gapped_lines=1))
            budget.observe(final)
            poll.return_value = final
            self.composition.analyzer_view_model.stop()
            self.wait(lambda: self.composition.analyzer_presenter.can_close())
            self.assertIs(self.composition.analyzer_view_model.state.sweep_snapshot, final)
            self.assertEqual(self.events, ["sweep-start", "sweep-stop"])
            self.assertFalse(self.composition.analyzer_view_model.state.running)
            self.assertEqual(self.composition.allocation_budget.snapshot().reserved_bytes, 0)

    def test_normal_composition_shares_one_ledger_and_low_budget_does_not_block_stop(self):
        self.select_and_apply()
        budget = self.composition.allocation_budget
        scene = self.page.visualization.spectrum_scene
        for owner in (self.presenter._snapshot_preparer, self.composition.analyzer_presenter._snapshot_preparer,
                      self.composition.spectrum_projector, scene._persistence,
                      self.page.visualization.waterfall_pane._renderer):
            self.assertIs(owner.allocation_budget, budget)
        self.page.primary.click()
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        budget.limit_bytes = 1
        frame = measurement(self)
        self.live._snapshot = frame
        self.presenter._emit_snapshot(frame)
        self.wait(lambda: self.composition.view_model.state.snapshot.presentation_omission is not None)
        self.assertIsNone(self.composition.view_model.state.spectrum)
        self.assertEqual(self.composition.view_model.state.measurement_unavailable_reason,
                         "presentation_memory_budget")
        self.assertTrue(self.live.is_running())
        self.page.primary.click()
        self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop"])
        self.assertEqual(self.composition.view_model.state.snapshot.state, LiveSessionState.CONNECTED)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_preparation_refuses_density_allocation_but_preserves_exact_spectrum(self):
        self.select_and_apply()
        snap = measurement(self)
        frame = replace(snap.spectrum, acquisition_epoch=1, receiver_id="test-rx", clock_domain="unknown")
        density = LivePersistenceFrame(
            update_sequence=1, timestamp_ns=1, source_frame_sequence=frame.sequence,
            power_min_db=-120, power_max_db=0, power_bins=4, frequency_bins=frame.fft_size,
            processed_frames=1, exponential_decay=False, frequencies_hz=frame.frequencies_hz,
            density=np.full((4, frame.fft_size), .25, dtype=np.float32),
            source_id=frame.source_id, config_generation=frame.config_generation, unit=frame.unit,
            producer_identity_available=True, acquisition_epoch=1, receiver_id="test-rx", clock_domain="unknown")
        snap = replace(snap, spectrum=frame, persistence=density)
        budget = self.composition.allocation_budget
        budget.observe(snap)
        self.presenter._snapshot_preparer._grid.prepare(frame.frequencies_hz)
        budget.limit_bytes = 1
        with patch("sdr_monitor.ui.v2.state.analyzer_layer_cache.persistence_density_from_native",
                   side_effect=AssertionError("conversion before admission")):
            self.presenter._emit_snapshot(snap)
            self.wait(lambda: self.composition.view_model.state.spectrum is frame)
        state = self.composition.view_model.state
        self.assertIsNone(state.persistence_frame)
        self.assertIs(state.snapshot, snap)
        self.assertIs(state.analyzer_bundle.spectrum, frame)
        self.assertEqual(state.measurement_unavailable_reason, "presentation_memory_budget")


if __name__ == "__main__":
    unittest.main()
