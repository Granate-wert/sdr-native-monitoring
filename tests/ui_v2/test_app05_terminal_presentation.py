"""Actual V2 Close releases UI payloads, not stopped/hidden/retryable measurements."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch
import weakref

import numpy as np

from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.shell.contracts import ClosePort
from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
from scripts.benchmark_app04_poll_overload import run_qt_until
from tests import test_app02_analyzer_workspace_product as product


class TerminalPresentationTests(unittest.TestCase):
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def wait(self, predicate):
        run_qt_until(predicate, 3)

    def measurement(self, *, visual=False):
        self.select_and_apply()
        self.page.primary.click()
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        snapshot = self.live.latest_snapshot()
        config = snapshot.applied.applied
        frame = LiveSpectrumFrame(sequence=1, timestamp_ns=1, source_id="fake-pluto-usb",
            config_generation=snapshot.generation, center_frequency_hz=config.center_hz,
            sample_rate_hz=61.44e6, fft_size=4096, hop_size=4096,
            frequencies_hz=config.center_hz + (np.arange(4096) - 2048) * (61.44e6 / 4096),
            values=np.full(4096, -70, np.float32), unit="dBFS/bin")
        snapshot = replace(snapshot, spectrum=frame, persistence=synthetic_persistence(frame, 32, 1, 1))
        self.live._snapshot = snapshot
        self.presenter.offer_snapshot_for_render(snapshot)
        scene = self.page.visualization.spectrum_scene
        pane = self.page.visualization.waterfall_pane
        if visual:
            scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        self.wait(lambda: scene.displayed_frame is not None and scene._persistence.metrics.image_uploads > 0
                  and pane.history_rows > 0)
        if visual:
            frame = replace(frame, sequence=2, timestamp_ns=2)
            snapshot = replace(snapshot, spectrum=frame, persistence=synthetic_persistence(frame, 32, 2, 2))
            self.live._snapshot = snapshot
            self.presenter.offer_snapshot_for_render(snapshot)
            self.wait(lambda: scene._persistence._row_scratch is not None)
        self.page.primary.click()
        self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
        self.wait(lambda: self.composition.spectrum_projector._future is None)

    def assert_payloads_released(self):
        snapshot = self.composition.memory_snapshot(self.page)
        self.assertEqual(snapshot.unique_array_bytes, 0, snapshot)
        self.assertIsNone(self.page.visualization.waterfall_pane._renderer.buffer)
        self.assertIsNone(self.composition.view_model.state.snapshot)
        self.assertIsNone(self.composition.analyzer_view_model.state.bundle)
        self.assertEqual(snapshot.allocation_budget.reserved_bytes, 0)
        scene = self.page.visualization.spectrum_scene
        for curve in (*scene._curves.values(), scene.sweep_coverage.history):
            self.assertIsNone(curve.curve.xData)
            self.assertIsNone(curve.curve.yData)

    def test_complete_close_releases_arrays_while_fixture_and_qobjects_stay_alive(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        pane = self.page.visualization.waterfall_pane
        density = weakref.ref(scene._persistence._latest_view.density)
        ring = weakref.ref(pane._renderer.buffer._data)
        grid = weakref.ref(scene.measurement_grid)
        curve_x = weakref.ref(scene._curves[TraceKind.CURRENT].curve.xData)
        self.assertGreater(self.composition.memory_snapshot(self.page).unique_array_bytes, 0)
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        self.assert_payloads_released()
        self.assertIsNone(density())
        self.assertIsNone(ring())
        self.assertIsNone(grid())
        self.assertIsNone(curve_x())
        # The source is external to UI presentation: closing a view must not
        # mutate its immutable backend snapshot to make ledger bytes zero.
        from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays
        source_roots = retained_arrays(self.live.latest_snapshot())
        self.assertEqual(set(self.composition.allocation_budget._roots), set(source_roots))
        self.assertEqual(self.composition.allocation_budget.snapshot().observed_bytes,
                         sum(source_roots.values()))
        self.shell.close()  # Repeated close must not resurrect storage or repeat backend work.
        self.assert_payloads_released()

    def test_stop_and_hide_preserve_last_frame_and_history(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        pane = self.page.visualization.waterfall_pane
        frame, ring = scene.latest_frame, pane._renderer.buffer
        self.shell.select_workspace("calibration")
        self.app.processEvents()
        self.shell.select_workspace("analyzer")
        self.wait(lambda: scene.displayed_frame is not None)
        self.assertIs(scene.latest_frame, frame)
        self.assertIs(pane._renderer.buffer, ring)
        self.assertGreater(pane.history_rows, 0)
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop"])

    def test_failed_close_retains_measurement_until_successful_retry(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        before = scene.latest_frame
        original = self.presenter._use_cases.shutdown
        calls = []

        def fail_once(timeout):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("terminal release test")
            original(timeout)

        with patch.object(self.presenter._use_cases, "shutdown", side_effect=fail_once):
            self.shell.close()
            self.wait(lambda: self.composition.close_lifecycle.state.phase == "failed")
            self.assertFalse(self.shell._is_closed)
            self.assertIs(scene.latest_frame, before)
            self.assertIsNotNone(self.composition.view_model.state.snapshot)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)
        self.assert_payloads_released()
        self.assertEqual(len(calls), 2)

    def test_pending_timeout_retains_measurement_until_worker_ack(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        before = scene.latest_frame
        release, entered = threading.Event(), threading.Event()
        self.composition.close_lifecycle.timeout_s = .03
        original = self.presenter._use_cases.shutdown

        def slow(timeout):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")
            original(timeout)

        try:
            with patch.object(self.presenter._use_cases, "shutdown", side_effect=slow):
                self.shell.close()
                self.wait(lambda: entered.is_set() and self.composition.close_lifecycle.state.phase == "timeout")
                self.assertIs(scene.latest_frame, before)
                self.assertIsNotNone(self.composition.view_model.state.snapshot)
                self.assertFalse(self.shell._is_closed)
                release.set()
                self.wait(lambda: self.shell._is_closed)
        finally:
            release.set()
        self.assert_payloads_released()

    def test_sweep_terminal_and_preparation_grid_release(self):
        self.measurement()
        self.page.mode.setCurrentIndex(self.page.mode.findData(AnalyzerMode.SWEEP))
        self.page.primary.click()
        self.wait(lambda: self.page._last_bundle is not None and self.page._last_bundle.mode == "sweep")
        self.page.primary.click()
        self.wait(lambda: self.composition.analyzer_presenter.can_close())
        self.assertIsNotNone(self.composition.analyzer_view_model.state.prepared_sweep)
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        self.assert_payloads_released()

    def test_visual_buffers_and_destroyed_inspector_wrapper_release(self):
        self.measurement(visual=True)
        scene = self.page.visualization.spectrum_scene
        density = weakref.ref(scene._persistence._latest_view.density)
        visual = weakref.ref(scene._persistence._visual_buffer)
        scratch = weakref.ref(scene._persistence._row_scratch)
        self.shell._inspector_toggle.click()
        from sdr_monitor.ui.v2.workspaces.analyzer_inspector import AnalyzerInspector
        inspectors = self.shell.findChildren(AnalyzerInspector)
        self.assertEqual(len(inspectors), 1)
        inspector = inspectors[0]  # Keep Python wrapper alive across normal Qt deletion.
        self.assertIsNotNone(inspector._state.bundle)
        self.shell.close()
        self.wait(lambda: self.shell._is_closed and inspector._state is None)
        self.assert_payloads_released()
        self.assertTrue(all(ref() is None for ref in (density, visual, scratch)))

    def test_late_prepared_callbacks_cannot_restore_closed_payloads(self):
        self.measurement()
        live_state = self.composition.view_model.state
        analyzer_state = self.composition.analyzer_view_model.state
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        live = self.composition.view_model
        analyzer = self.composition.analyzer_view_model
        live._on_snapshot(live_state.snapshot)
        live._on_prepared_snapshot(live_state)
        live._on_busy_changed(True)
        live._on_task_failed("late")
        analyzer._on_live(live_state)
        analyzer._on_running(True)
        self.page._render(analyzer_state)
        self.assert_payloads_released()

    def test_workspace_release_waits_for_all_shell_ports_and_retry_is_idempotent(self):
        self.measurement()
        before = self.page.visualization.spectrum_scene.latest_frame
        attempts = []

        def extra():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("extra owner failed")

        self.shell._context = replace(self.shell._context, close_ports=(
            *self.shell._context.close_ports, ClosePort("extra", lambda: True, extra)))
        with patch.object(self.page, "release_presentation_after_shutdown",
                          wraps=self.page.release_presentation_after_shutdown) as cleanup:
            self.shell.close()
            self.wait(lambda: len(attempts) == 1)
            self.assertFalse(self.shell._is_closed)
            cleanup.assert_not_called()
            self.assertIs(self.page.visualization.spectrum_scene.latest_frame, before)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)
            cleanup.assert_called_once()
            self.shell.close()
            cleanup.assert_called_once()
        self.assert_payloads_released()

    def test_terminal_workspace_cleanup_failure_remains_retryable_without_owner_restart(self):
        self.measurement()
        original = self.page.release_presentation_after_shutdown
        attempts = []

        def fail_once():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("local cleanup failed")
            original()

        with patch.object(self.page, "release_presentation_after_shutdown", side_effect=fail_once):
            self.shell.close()
            self.wait(lambda: len(attempts) == 1)
            self.assertFalse(self.shell._is_closed)
            after_owner_close = list(self.events)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)
        self.assert_payloads_released()
        self.assertEqual(self.events, after_owner_close)

    def test_early_release_is_rejected_without_mutating_measurement(self):
        self.measurement()
        before = self.page.visualization.spectrum_scene.latest_frame
        for owner in (self.presenter, self.composition.analyzer_presenter,
                      self.composition.view_model, self.composition.analyzer_view_model,
                      self.composition.spectrum_projector):
            with self.assertRaises(RuntimeError):
                owner.release_presentation_after_shutdown()
        self.assertIs(self.page.visualization.spectrum_scene.latest_frame, before)

    def test_inventory_counts_hidden_internal_plot_arrays(self):
        self.measurement()
        curve = self.page.visualization.spectrum_scene._curves[TraceKind.CURRENT]
        # Reproduce pyqtgraph's empty parent dataset with a retained child.
        curve.setData([], [])
        self.assertIsNone(curve.xData)
        self.assertIsNotNone(curve.curve.xData)
        report = self.composition.memory_snapshot(self.page)
        sizes = {owner.name: owner.bytes for owner in report.owners}
        self.assertGreaterEqual(sizes["spectrum.plot-arrays"],
                                curve.curve.xData.nbytes + curve.curve.yData.nbytes)
        curve.clear()

    def test_terminal_projection_releases_done_result_before_queued_callback(self):
        from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
        from tests.ui_v2.test_app05_projection_cancellation import request
        from tests.ui_v2.test_app05_viewport_projection import ManualWorker
        from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
        worker = ManualWorker()
        budget = PresentationAllocationBudget()
        port = SpectrumProjector(worker.submit, allocation_budget=budget)
        self.addCleanup(port.dispose)
        delivered = []
        port.ready.connect(delivered.append)
        port.offer(request())
        worker.finish()  # joins fake worker, Qt completion is still queued
        self.assertIsNotNone(port._future)
        port.dispose()
        port.release_presentation_after_shutdown()
        self.assertIsNone(port._future)
        self.assertEqual(port.retained_bytes, 0)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.app.processEvents()  # queued callback cannot resurrect the detached result
        self.assertEqual(delivered, [])
