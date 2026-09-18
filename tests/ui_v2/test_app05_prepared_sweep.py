"""Worker-owned Sweep display preparation: pure/fake Qt, no receiver."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.state.prepared_sweep import prepare_sweep_snapshot
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product
from tests import test_app04_sweep_poll_responsiveness as worker
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


class PreparedSweepTests(unittest.TestCase):
    def test_terminal_and_next_preview_keep_exact_row_semantics(self):
        snapshot = ContinuousSweepDisplaySnapshot(terminal(gap=True), ContinuousSweepDisplayMetrics(), progress(2))
        bundle = snapshot.analyzer_bundle
        prepared = prepare_sweep_snapshot(snapshot, bundle)
        self.assertIs(prepared.snapshot, snapshot)
        self.assertIs(prepared.analyzer_bundle, bundle)
        self.assertIs(prepared.spectrum.view.source_frame, bundle)
        self.assertTrue(np.shares_memory(prepared.spectrum.view.values, bundle.values))
        self.assertEqual(len(prepared.waterfall_rows), 2)
        for frame, actual in zip((snapshot.line, snapshot.progress), prepared.waterfall_rows):
            expected = waterfall_line_from_sweep(frame)
            self.assertEqual(actual.stamp, expected.stamp)
            np.testing.assert_array_equal(actual.row.values, expected.row.values)
            np.testing.assert_array_equal(actual.row.frequency_edges_hz, expected.row.frequency_edges_hz)
            self.assertFalse(actual.row.timestamp_known)
            self.assertFalse(actual.row.values.flags.writeable)
        self.assertIsNone(prepared.waterfall_error)

    def test_invalid_regular_grid_returns_no_stale_rows_and_explicit_error(self):
        frequencies = np.array([100e6, 101e6, 102.2e6, 103e6])
        frequencies.setflags(write=False)
        frame = replace(progress(), frequencies_hz=frequencies)
        snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), frame)
        result = prepare_sweep_snapshot(snapshot, snapshot.analyzer_bundle)
        self.assertEqual(result.waterfall_rows, ())
        self.assertIn("regular physical grid", result.waterfall_error)

    def test_packet_rejects_bundle_from_another_snapshot(self):
        snapshot = ContinuousSweepDisplaySnapshot(terminal(), ContinuousSweepDisplayMetrics())
        other = ContinuousSweepDisplaySnapshot(terminal(2), ContinuousSweepDisplayMetrics())
        with self.assertRaisesRegex(ValueError, "exact snapshot"):
            prepare_sweep_snapshot(snapshot, other.analyzer_bundle)

    def test_actual_composition_prepares_bundle_and_rows_off_gui_without_fallback(self):
        gui = threading.get_ident()
        preparation_threads, bundle_threads = [], []
        bundle_property = ContinuousSweepDisplaySnapshot.analyzer_bundle.fget

        def prepare(snapshot, bundle):
            preparation_threads.append(threading.get_ident())
            return prepare_sweep_snapshot(snapshot, bundle)

        def bundle(snapshot):
            bundle_threads.append(threading.get_ident())
            return bundle_property(snapshot)

        harness = product.AnalyzerWorkspaceProductTests("runTest")
        harness.app = QApplication.instance() or QApplication([])
        with patch("sdr_monitor.ui.v2.state.prepared_sweep.prepare_sweep_snapshot", prepare):
            harness.setUp()
        try:
            harness.select_and_apply()
            page = harness.page
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
            with patch.object(ContinuousSweepDisplaySnapshot, "analyzer_bundle", property(bundle)), \
                 patch("sdr_monitor.ui.v2.spectrum.scene.adapt_spectrum_frame",
                       side_effect=AssertionError("GUI grid validation fallback used")), \
                 patch("sdr_monitor.ui.v2.workspaces.analyzer.waterfall_line_from_sweep",
                       side_effect=AssertionError("GUI projection fallback used")):
                page.primary.click()
                harness.wait(lambda: page.visualization.waterfall_pane.history_rows > 0)
                state = harness.composition.analyzer_view_model.state
                self.assertIs(state.prepared_sweep.snapshot, state.sweep_snapshot)
                self.assertIs(state.bundle, state.prepared_sweep.analyzer_bundle)
                page.primary.click()
                harness.wait(harness.composition.analyzer_presenter.can_close)
            self.assertTrue(preparation_threads)
            self.assertTrue(bundle_threads)
            self.assertTrue(all(t != gui for t in preparation_threads + bundle_threads))
            self.assertEqual(len(set(preparation_threads + bundle_threads)), 1)
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.RTBW))
            self.assertIsNone(harness.composition.analyzer_view_model.state.prepared_sweep)
            self.assertEqual(harness.events, ["sweep-start", "sweep-stop"])
        finally:
            harness.tearDown()
            harness.doCleanups()

    def test_prepared_spectrum_rejects_mutable_input_and_mismatched_delivery(self):
        from tests.ui_v2.test_spectrum_scene import SyntheticSpectrumFrame
        values, frequencies = np.ones(4), np.arange(4.)
        with self.assertRaisesRegex(ValueError, "read-only"):
            PreparedSpectrumFrame(SyntheticSpectrumFrame(frequencies, values))
        values.setflags(write=False)
        frequencies.setflags(write=False)
        frame = SyntheticSpectrumFrame(frequencies, values)
        prepared = PreparedSpectrumFrame(frame)
        app = QApplication.instance() or QApplication([])
        scene = SpectrumScene()
        try:
            scene.set_frame(frame, prepared=prepared)
            self.assertIs(scene.latest_frame, frame)
            with self.assertRaisesRegex(ValueError, "exact publication"):
                scene.set_frame(replace(frame, unit="dBFS/bin"), prepared=prepared)
            self.assertIs(scene.latest_frame, frame)
        finally:
            scene.close()
            scene.deleteLater()
            app.processEvents()

    def test_delayed_preparation_is_single_flight_and_stop_preserves_order(self):
        fixture = worker.SweepPollResponsivenessTests("runTest")
        fixture.app = QApplication.instance() or QApplication([])
        service = worker._DelayedDisplay()
        service.release.set()
        entered, release = threading.Event(), threading.Event()
        calls, delivered = [], []

        def prepare(snapshot, bundle):
            calls.append(threading.get_ident())
            if len(calls) == 1:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test presentation barrier expired")
            return prepare_sweep_snapshot(snapshot, bundle)

        presenter = ContinuousSweepPresenter(service, snapshot_preparer=prepare, max_poll_hz=1000)
        presenter.prepared_snapshot_ready.connect(lambda p: delivered.append(p.snapshot.line.sequence))
        try:
            presenter.start(object())
            fixture.wait(lambda: not presenter.is_starting)
            presenter._poll()
            fixture.wait(entered.is_set)
            for _ in range(1000):
                presenter._poll()
            self.assertEqual(service.polls, 1)
            self.assertNotEqual(calls[0], threading.get_ident())
            presenter.stop()
            self.assertTrue(presenter.is_stopping)
            self.assertFalse(presenter.can_close())
            release.set()
            presenter._stop_future.result(timeout=2)
            presenter._finish_stop(presenter._stop_future)
            fixture.app.processEvents()
            self.assertEqual(delivered, [1, 2])
            self.assertTrue(presenter.can_close())
            self.assertEqual(service.calls.count("stop"), 1)
        finally:
            release.set()
            presenter.shutdown()

    def test_preparation_failure_stops_once_and_never_delivers_bad_packet(self):
        fixture = worker.SweepPollResponsivenessTests("runTest")
        fixture.app = QApplication.instance() or QApplication([])
        service = worker._DelayedDisplay()
        service.release.set()
        count = 0

        def prepare(snapshot, bundle):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("injected preparation failure")
            return prepare_sweep_snapshot(snapshot, bundle)

        presenter = ContinuousSweepPresenter(service, snapshot_preparer=prepare, max_poll_hz=1000)
        errors, delivered = [], []
        presenter.task_failed.connect(errors.append)
        presenter.prepared_snapshot_ready.connect(lambda p: delivered.append(p.snapshot.line.sequence))
        try:
            presenter.start(object())
            fixture.wait(lambda: not presenter.is_starting)
            presenter._poll()
            fixture.wait(presenter.can_close)
            self.assertEqual(errors, ["injected preparation failure"])
            self.assertEqual(delivered, [2])
            self.assertEqual(service.calls.count("stop"), 1)
        finally:
            presenter.shutdown()
