"""Pre-holder source admission in the actual V2 composition; no hardware."""
from dataclasses import replace
import gc
import threading
import unittest
import weakref
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.presentation_omission import OmittedMeasurement, PresentationOmission
from sdr_monitor.ui.v2.i18n import text
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays
from sdr_monitor.ui.v2.state.source_admission import PresentationSourceAdmission
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests.ui_v2.test_app05_prepared_live import measurement


class SourceBudgetTests(unittest.TestCase):
    def test_omission_record_rejects_arrays_and_mutable_collections(self):
        record = OmittedMeasurement("source", 1, 2, "dBFS/bin", "gap")
        with self.assertRaises(TypeError):
            replace(record, source_id=np.zeros(10))
        with self.assertRaises(ValueError):
            replace(record, gap_reasons=["cancelled"])
        with self.assertRaises(ValueError):
            PresentationOmission(10, 1, [record])
        with self.assertRaises(ValueError):
            PresentationOmission(10, 1, (record,) * 3)

    def test_atomic_admission_deduplicates_slices_and_competes_with_reservations(self):
        budget = PresentationAllocationBudget(100)
        root = np.zeros(60, np.uint8)
        self.assertTrue(budget.admit_sources(root[:1]))
        self.assertTrue(budget.admit_sources(root[1:2]))
        with budget.reserve(30):
            rejected = np.zeros(11, np.uint8)
            self.assertFalse(budget.admit_sources(rejected))
            self.assertTrue(budget.admit_sources(None))
            self.assertEqual(budget.snapshot().observed_bytes, 60)
        self.assertTrue(budget.admit_sources(rejected))
        del root, rejected
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 0)

    def test_two_workers_cannot_admit_beyond_remaining_capacity(self):
        budget = PresentationAllocationBudget(100)
        arrays = [np.zeros(60, np.uint8), np.zeros(60, np.uint8)]
        barrier = threading.Barrier(2)
        results = []
        def run(array):
            barrier.wait(timeout=3)
            results.append(budget.admit_sources(array))
        workers = [threading.Thread(target=run, args=(array,)) for array in arrays]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(4)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(budget.snapshot().observed_bytes, 60)

    def test_sweep_omission_keeps_terminal_and_next_preview_without_source_arrays(self):
        source = ContinuousSweepDisplaySnapshot(terminal(gap=True), ContinuousSweepDisplayMetrics(gapped_lines=1),
                                                progress(2))
        admission = PresentationSourceAdmission(PresentationAllocationBudget(1))
        omitted = admission.sweep(source)
        self.assertEqual(retained_arrays(omitted), {})
        self.assertIs(omitted.metrics, source.metrics)
        self.assertEqual([(r.sequence, r.epoch, r.state) for r in omitted.presentation_omission.measurements],
                         [(1, 7, "gap"), (2, 7, "partial")])
        self.assertEqual(omitted.presentation_omission.measurements[0].missing_segments, 2)
        self.assertIs(admission.sweep(omitted), omitted)
        self.assertEqual(admission.budget.snapshot().observed_bytes, 0)
        reference = weakref.ref(source.line.values_db)
        del source
        gc.collect()
        self.assertIsNone(reference())


class ProductSourceAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.f = product.AnalyzerWorkspaceProductTests("runTest")
        self.f.app = self.app
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.f.select_and_apply()

    def start_live(self):
        f = self.f
        f.page.primary.click()
        f.wait(lambda: f.live.is_running() and not f.composition.view_model.state.busy)

    def test_live_rejects_before_scheduler_poll_holder_and_worker_then_recovers(self):
        f = self.f
        self.start_live()
        budget = f.composition.allocation_budget
        budget.limit_bytes = 1
        snapshot = measurement(f)
        f.live._snapshot = snapshot
        f.presenter._poll_frames()
        for holder in (f.presenter._last_polled, f.presenter._display_scheduler._pending):
            self.assertIsNotNone(holder.presentation_omission)
            self.assertEqual(retained_arrays(holder), {})
            self.assertIs(holder.quality, snapshot.quality)
            self.assertEqual(holder.state, snapshot.state)
        f.wait(lambda: f.composition.view_model.state.measurement_unavailable_reason == "presentation_memory_budget")
        self.assertIsNone(f.page.visualization.spectrum_scene.latest_frame)
        self.assertIn(text("analyzer.source_memory_limited"), f.page.status.text())
        self.assertIsNone(f.composition.analyzer_view_model.state.error)
        budget.limit_bytes = 256 * 1024 * 1024
        restored = replace(measurement(f, 2), sequence=snapshot.sequence + 1)
        f.live._snapshot = restored
        f.presenter.offer_snapshot_for_render(restored)
        f.wait(lambda: f.composition.view_model.state.spectrum is restored.spectrum)
        self.assertIs(f.page._last_bundle.spectrum, restored.spectrum)
        self.assertIsNone(f.composition.view_model.state.measurement_unavailable_reason)

    def test_live_stop_completes_under_pressure_and_clears_old_plot_history(self):
        f = self.f
        self.start_live()
        snapshot = measurement(f)
        f.live._snapshot = snapshot
        f.presenter.offer_snapshot_for_render(snapshot)
        f.wait(lambda: f.page.visualization.waterfall_pane.history_rows > 0)
        failures = []
        f.presenter.task_failed.connect(failures.append)
        f.composition.allocation_budget.limit_bytes = 1
        # A genuinely new oversized source must not enter even the Stop result.
        f.live._snapshot = measurement(f, 2)
        f.page.primary.click()
        f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
        self.assertIsNone(f.page.visualization.spectrum_scene.latest_frame)
        self.assertEqual(f.page.visualization.waterfall_pane.history_rows, 0)
        self.assertIsNotNone(f.composition.view_model.state.snapshot.presentation_omission)
        self.assertEqual(failures, [])
        self.assertTrue(f.composition.can_close())

    def test_sweep_stop_delivers_omitted_real_terminal_without_stale_measurement(self):
        f = self.f
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
        f.page.primary.click()
        f.wait(lambda: f.page._last_bundle is not None)
        sweep = f.composition.analyzer_presenter
        failures = []
        sweep.task_failed.connect(failures.append)
        snapshot = ContinuousSweepDisplaySnapshot(terminal(gap=True), ContinuousSweepDisplayMetrics(gapped_lines=1))
        f.composition.allocation_budget.limit_bytes = 1
        with patch.object(sweep._service, "poll_latest", return_value=snapshot):
            f.page.primary.click()
            f.wait(lambda: sweep.can_close())
        state = f.composition.analyzer_view_model.state
        self.assertIsNone(state.bundle)
        self.assertIsNone(f.page.visualization.spectrum_scene.latest_frame)
        self.assertEqual(f.page.visualization.waterfall_pane.history_rows, 0)
        record = state.sweep_snapshot.presentation_omission.measurements[0]
        self.assertEqual((record.epoch, record.sequence, record.state), (7, 1, "gap"))
        self.assertEqual(state.sweep_snapshot.metrics.gapped_lines, 1)
        self.assertIn(text("analyzer.source_memory_limited"), f.page.status.text())
        self.assertFalse(state.running)
        self.assertEqual(failures, [])

    def test_pending_preparation_cannot_hold_rejected_source(self):
        f = self.f
        f.composition.allocation_budget.limit_bytes = 1
        snapshot = measurement(f)
        f.presenter._pending_commands = 1
        try:
            f.presenter._offer_preparation(snapshot, f.presenter._control_revision)
            self.assertEqual(retained_arrays(f.presenter._pending_preparation[0]), {})
        finally:
            f.presenter._pending_commands = 0
            f.presenter._pending_preparation = None


if __name__ == "__main__":
    unittest.main()
