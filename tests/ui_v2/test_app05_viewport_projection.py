"""Bounded viewport worker, exact source markers and obsolete-result barriers."""
from concurrent.futures import Future
from dataclasses import dataclass
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame, SpectrumFrameView, TraceKind
from sdr_monitor.ui.v2.spectrum.envelope import peak_preserving_envelope
from sdr_monitor.ui.v2.spectrum.projection import ProjectionRequest, SpectrumProjector, project_spectrum
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.spectrum.sweep_coverage import SweepCoverageState
from tests.ui_v2.test_app04_sweep_coverage import line, snapshot


@dataclass(frozen=True)
class DisplayFrame:
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str


class ManualWorker:
    def __init__(self):
        self.jobs = []

    def submit(self, operation):
        future = Future()
        self.jobs.append((future, operation))
        return future

    def finish(self, error=None):
        future, operation = self.jobs.pop(0)

        def run():
            try:
                if error is not None:
                    raise error
                future.set_result(operation())
            except Exception as exc:
                future.set_exception(exc)

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(3)
        assert not worker.is_alive()


class ViewportProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.worker = ManualWorker()
        self.port = SpectrumProjector(self.worker.submit)
        self.scene = SpectrumScene()
        # Keep tick-label relayout out of these deterministic queue barriers;
        # real geometry churn is covered explicitly by the resize test below.
        self.scene.plot_item.getAxis("left").setWidth(80)
        self.scene.set_projection_port(self.port)
        self.scene.resize(1100, 600)
        self.scene.show()
        self.pump()

    def tearDown(self):
        self.port.dispose()
        self.scene.close()
        self.scene.deleteLater()
        self.pump()

    def pump(self):
        for _ in range(8):
            self.app.processEvents()

    def admit(self, sequence=1, value=-70, epoch=1):
        source = line(sequence, np.full(65536, value, dtype=np.float32), epoch=epoch)
        frame = DisplayFrame(source.frequencies_hz, source.values_db, source.unit)
        self.scene.set_frame(frame, prepared=PreparedSpectrumFrame(frame))
        self.pump()
        return frame

    def drain(self):
        for _ in range(8):
            self.pump()
            if not self.worker.jobs:
                return
            self.worker.finish()
        self.fail("projection did not settle")

    def test_exact_reducer_and_coverage_parity_for_zoom_nan_infinity_and_tail(self):
        values = np.sin(np.arange(200003) / 130).astype(np.float32)
        values[::11] = np.nan
        values[2000:3000] = -np.inf
        values[100001] = 100
        current = line(2, values)
        previous = line(1, np.full(values.size, -20))
        view = PreparedSpectrumFrame(DisplayFrame(current.frequencies_hz, current.values_db, current.unit)).view
        state = SweepCoverageState()
        state.accept(snapshot(previous))
        state.accept(snapshot(current))
        for left, right, width in ((0, 1e12, 17), (130e6, 131e6, 911), (500e6, 600e6, 31)):
            request = ProjectionRequest(object(), 1, (left, right, width),
                                        ((TraceKind.CURRENT, view),), current, previous)
            actual = project_spectrum(request)
            start = max(0, np.searchsorted(view.frequencies_hz, left) - 1)
            stop = min(view.point_count, np.searchsorted(view.frequencies_hz, right, side="right") + 1)
            if stop <= start:
                start, stop = 0, view.point_count
            visible = SpectrumFrameView(current, view.frequencies_hz[start:stop], view.values[start:stop], view.unit_label)
            expected = peak_preserving_envelope(visible, width)
            np.testing.assert_array_equal(actual.traces[0][1].values, expected.values)
            np.testing.assert_array_equal(actual.traces[0][1].frequencies_hz, expected.frequencies_hz)
            coverage = state.project(left, right, width)
            np.testing.assert_array_equal(actual.coverage.states, coverage.states)
            np.testing.assert_array_equal(actual.coverage.history.values, coverage.history.values)
            self.assertFalse(actual.traces[0][1].values.flags.writeable)

    def test_one_active_one_latest_slot_is_held_until_gui_ack(self):
        frame = self.admit()
        request = self.port._active
        for _ in range(100):
            self.port.offer(request)
        self.assertEqual(len(self.worker.jobs), 1)
        self.assertEqual(self.port.superseded, 99)
        self.worker.finish()
        self.assertEqual(len(self.worker.jobs), 0)
        self.assertIsNotNone(self.port._future)
        self.pump()
        self.assertIs(self.scene.displayed_frame, frame)
        self.assertEqual(len(self.worker.jobs), 1)
        self.drain()

    def test_newer_arrival_does_not_starve_paint_and_markers_follow_displayed_source(self):
        first = self.admit(1, -71)
        second = self.admit(2, -32)
        self.assertIsNone(self.scene.place_marker("M1", 105e6))
        self.worker.finish()
        self.pump()
        self.assertIs(self.scene.latest_frame, second)
        self.assertIs(self.scene.displayed_frame, first)
        self.assertEqual(self.scene.place_marker("M1", 105e6).value, -71)
        self.drain()
        self.assertIs(self.scene.displayed_frame, second)
        self.assertEqual(self.scene.markers[0].value, -32)

    def test_zoom_and_resize_reject_obsolete_projection_and_reproject_exact_source(self):
        frame = self.admit()
        self.scene.plot_item.setXRange(110e6, 120e6, padding=0)
        self.scene.resize(800, 600)
        self.pump()
        self.worker.finish()
        self.pump()
        self.assertIsNone(self.scene.displayed_frame)
        self.assertGreater(self.scene.projection_stale, 0)
        self.drain()
        self.assertIs(self.scene.displayed_frame, frame)
        envelope = self.scene.trace_envelope(TraceKind.CURRENT)
        self.assertLessEqual(envelope.display_point_count, 9 * int(self.scene.view_box.width()))
        self.assertLessEqual(np.nanmax(envelope.frequencies_hz), 120.002e6)

    def test_hide_clear_and_new_epoch_never_resurrect_old_layers(self):
        self.admit()
        self.scene.set_presentation_active(False)
        self.worker.finish()
        self.pump()
        self.assertIsNone(self.scene.displayed_frame)
        self.scene.clear_measurement()
        new = self.admit(2, -40, epoch=2)
        self.assertEqual(self.worker.jobs, [])
        self.scene.set_presentation_active(True)
        self.drain()
        self.assertIs(self.scene.displayed_frame, new)
        self.admit(3)
        self.scene.clear_measurement()
        self.worker.finish()
        self.pump()
        self.assertIsNone(self.scene.displayed_frame)
        self.assertIsNone(self.scene.trace_envelope(TraceKind.CURRENT))

    def test_failure_visible_recovery_and_dispose_suppress_queued_callback(self):
        self.admit()
        self.worker.finish(RuntimeError("fixture failure"))
        self.pump()
        self.assertIn("fixture failure", self.scene._warning_readout.text())
        self.admit(2)
        self.drain()
        self.assertEqual(self.scene._warning_readout.text(), "")
        displayed = self.scene.displayed_frame
        self.admit(3)
        self.worker.finish()
        self.port.dispose()
        self.pump()
        self.assertIs(self.scene.displayed_frame, displayed)

    def test_rejects_mutable_arrays_without_changing_them(self):
        values = np.zeros(10)
        view = SpectrumFrameView(object(), np.arange(10), values, "dBm")
        request = ProjectionRequest(object(), 1, (0, 10, 10), ((TraceKind.CURRENT, view),))
        with self.assertRaisesRegex(ValueError, "immutable"):
            project_spectrum(request)
        self.assertTrue(values.flags.writeable)

    def test_removed_average_cannot_reappear_from_completed_old_job(self):
        frame = self.admit()
        self.scene.set_trace(TraceKind.AVERAGE, frame)
        self.pump()
        self.scene.clear_trace(TraceKind.AVERAGE)
        self.pump()
        self.worker.finish()
        self.pump()
        self.assertIsNone(self.scene.displayed_frame)
        self.drain()
        self.assertIs(self.scene.displayed_frame, frame,
                      (self.scene._projection_generation, self.scene._projection_key, self.scene._viewport(),
                       self.port._future, self.port._pending, self.scene.projection_stale))
        self.assertIsNone(self.scene.trace_envelope(TraceKind.AVERAGE))

    def test_late_layout_change_reissues_latest_without_another_source_frame(self):
        frame = self.admit()
        left, right, width = self.scene._viewport()
        with patch.object(self.scene, "_viewport", return_value=(left, right, width - 1)):
            self.worker.finish()
            self.pump()
            self.assertIsNone(self.scene.displayed_frame)
            self.assertEqual(len(self.worker.jobs), 1)
            self.drain()
            self.assertIs(self.scene.displayed_frame, frame)

    def test_immediate_future_delivery_is_still_queued_and_dispose_drops_it(self):
        self.admit()
        request = self.port._active

        def immediate(operation):
            future = Future()
            future.set_result(operation())
            return future

        port = SpectrumProjector(immediate)
        delivered = []
        port.ready.connect(delivered.append)
        port.offer(request)
        self.assertEqual(delivered, [])
        self.assertIsNotNone(port._future)
        port.dispose()
        self.pump()
        self.assertEqual(delivered, [])

    def test_history_label_and_scale_follow_displayed_not_new_pending_frame(self):
        self.admit(2, -80)
        old = line(1, np.full(65536, -20))
        current = line(2, np.r_[np.full(32768, -80), np.full(32768, np.nan)])
        self.scene.sweep_coverage.accept(snapshot(old))
        self.scene.sweep_coverage.accept(snapshot(current))
        self.pump()
        self.drain()
        coverage = self.scene.sweep_coverage
        self.assertIs(coverage._display_previous, old)
        self.assertLess(self.scene._reference_level, -60)
        self.admit(3, -40)
        coverage.accept(snapshot(line(3, np.r_[np.full(32768, -40), np.full(32768, np.nan)])))
        self.pump()
        self.assertIs(coverage._display_previous, old)
        self.assertLess(self.scene._reference_level, -60)
        self.drain()
        self.assertIs(coverage._display_previous, current)
        self.assertGreater(self.scene._reference_level, -40)


class ActualCompositionProjectionTests(unittest.TestCase):
    def test_normal_rtbw_and_sweep_share_worker_projection_not_gui_reducer(self):
        from tests import test_app02_analyzer_workspace_product as product
        from tests.ui_v2.test_app05_prepared_live import measurement
        from sdr_monitor.ui.v2.spectrum import projection
        from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode

        fixture = product.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = QApplication.instance() or QApplication([])
        fixture.setUp()
        try:
            fixture.select_and_apply()
            scene = fixture.page.visualization.spectrum_scene
            threads, coverage_threads = [], []
            reducer = projection.peak_preserving_envelope
            coverage = SweepCoverageState.project

            def reduce(*args):
                threads.append(threading.get_ident())
                return reducer(*args)

            def cover(state, *args):
                coverage_threads.append(threading.get_ident())
                return coverage(state, *args)

            with patch.object(projection, "peak_preserving_envelope", side_effect=reduce), \
                 patch.object(SweepCoverageState, "project", cover), \
                 patch("sdr_monitor.ui.v2.spectrum.scene.peak_preserving_envelope",
                       side_effect=AssertionError("GUI reducer forbidden")):
                fixture.page.primary.click()
                fixture.wait(lambda: fixture.live.is_running() and not fixture.composition.view_model.state.busy)
                source = measurement(fixture)
                fixture.live._snapshot = source
                fixture.presenter.offer_snapshot_for_render(source)
                fixture.wait(lambda: scene.displayed_frame is not None)
                self.assertIs(scene.displayed_frame.spectrum, source.spectrum)
                fixture.page.primary.click()
                fixture.wait(lambda: not fixture.live.is_running() and not fixture.composition.view_model.state.busy)
                fixture.page.mode.setCurrentIndex(fixture.page.mode.findData(AnalyzerMode.SWEEP))
                fixture.page.primary.click()
                fixture.wait(lambda: scene.sweep_coverage.projection is not None)
                self.assertEqual(scene.displayed_frame.mode, "sweep")
                self.assertTrue(coverage_threads)
                self.assertTrue(threads)
                self.assertTrue(all(t != threading.get_ident() for t in threads + coverage_threads))
                self.assertEqual(len(set(threads + coverage_threads)), 1)
        finally:
            fixture.tearDown()
            fixture.doCleanups()
