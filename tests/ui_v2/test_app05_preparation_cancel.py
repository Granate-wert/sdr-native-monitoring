"""Obsolete render preparation yields at coherent stages; Stop still prepares."""
from concurrent.futures import CancelledError
import threading
import unittest
from unittest.mock import patch

from sdr_monitor.domain import LiveSessionState
from sdr_monitor.ui.v2.state import live_view_state
from sdr_monitor.ui.v2.state.prepared_live import LiveSnapshotPreparer
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum import contracts
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app05_prepared_live import measurement


class PreparationCancellationTests(unittest.TestCase):
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    wait = product.AnalyzerWorkspaceProductTests.wait
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def test_actual_stop_skips_remaining_obsolete_layers_but_prepares_terminal_source(self):
        self.select_and_apply()
        self.page.primary.click()
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        old = measurement(self, 91)
        entered, release = threading.Event(), threading.Event()
        original_bundle = live_view_state.bundle_from_live
        current = {}
        layers, delivered, errors = [], [], []
        cache = self.presenter._snapshot_preparer._layers
        original_waterfall = cache.waterfall

        def bundle(snapshot):
            current["snapshot"] = snapshot
            value = original_bundle(snapshot)
            if snapshot.spectrum is old.spectrum and snapshot.state is LiveSessionState.RUNNING:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("preparation stage barrier")
            return value

        def waterfall(frame):
            layers.append(current["snapshot"])
            return original_waterfall(frame)

        self.presenter.prepared_snapshot_ready.connect(lambda value: delivered.append(value.snapshot))
        self.presenter.task_failed.connect(errors.append)
        with patch.object(live_view_state, "bundle_from_live", side_effect=bundle), \
             patch.object(cache, "waterfall", side_effect=waterfall):
            try:
                self.live._snapshot = old
                self.presenter.offer_snapshot_for_render(old)
                self.wait(entered.is_set)
                delivered.clear()
                self.page.primary.click()
                self.assertTrue(self.composition.view_model.state.busy)
                release.set()
                self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
                self.wait(lambda: self.presenter._preparation_future is None)
            finally:
                release.set()
        self.assertFalse(any(snapshot.spectrum is old.spectrum and
                             snapshot.state is LiveSessionState.RUNNING for snapshot in layers))
        self.assertTrue(any(snapshot.state is LiveSessionState.CONNECTED and
                            snapshot.spectrum is old.spectrum for snapshot in layers))
        self.assertTrue(delivered)
        self.assertTrue(all(snapshot.state is not LiveSessionState.RUNNING for snapshot in delivered))
        self.assertEqual(errors, [])
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop"])
        self.assertEqual(self.composition.allocation_budget.snapshot().reserved_bytes, 0)

    def test_cancel_after_persistence_does_not_build_waterfall_or_poison_next_preparation(self):
        self.select_and_apply()
        snapshot = measurement(self, 7)
        budget = PresentationAllocationBudget()
        preparer = LiveSnapshotPreparer(budget)
        cancelled = threading.Event()
        original = preparer._layers.persistence

        def persistence(frame):
            value = original(frame)
            cancelled.set()
            return value

        with patch.object(preparer._layers, "persistence", side_effect=persistence), \
             patch.object(preparer._layers, "waterfall") as waterfall:
            with self.assertRaises(CancelledError):
                preparer.prepare_cancellable(snapshot, cancelled=cancelled.is_set)
            waterfall.assert_not_called()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        cancelled.clear()
        result = preparer.prepare_cancellable(snapshot, cancelled=cancelled.is_set)
        self.assertIs(result.snapshot, snapshot)
        self.assertIs(result.analyzer_bundle.spectrum, snapshot.spectrum)
        self.assertIs(result.prepared_spectrum.view.source_frame, result.analyzer_bundle)
        self.assertIsNotNone(result.waterfall_line)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_cancel_after_adapter_skips_extent_and_keeps_grid_reusable(self):
        self.select_and_apply()
        snapshot = measurement(self, 8)
        preparer = LiveSnapshotPreparer(PresentationAllocationBudget())
        bundle = live_view_state.bundle_from_live(snapshot)
        cancelled = threading.Event()
        original = contracts.adapt_spectrum_frame

        def adapt(frame):
            result = original(frame)
            cancelled.set()
            return result

        with patch.object(contracts, "adapt_spectrum_frame", side_effect=adapt), \
             patch.object(contracts, "finite_value_extent") as extent:
            with self.assertRaises(CancelledError):
                contracts.PreparedSpectrumFrame(bundle, grid_cache=preparer._grid,
                                                cancelled=cancelled.is_set)
            extent.assert_not_called()
        self.assertEqual(preparer.allocation_budget.snapshot().reserved_bytes, 0)
        cancelled.clear()
        result = preparer.prepare_cancellable(snapshot, cancelled=cancelled.is_set)
        self.assertIs(result.prepared_spectrum.view.source_frame.spectrum, snapshot.spectrum)


if __name__ == "__main__":
    unittest.main()
