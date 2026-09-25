"""Two-phase close: actual product composition, deterministic non-RF owners."""
import threading
import time
import unittest
import weakref
from unittest.mock import Mock, patch
from dataclasses import replace

from PySide6.QtCore import QTimer

from sdr_monitor.ui.v2.shell.close_lifecycle import CloseLifecycle
from sdr_monitor.ui.v2.shell.contracts import ClosePort
from sdr_monitor.ui.presenters.diagnostics_presenter import DiagnosticsPresenter
from sdr_monitor.ui.presenters.replay_presenter import ReplayPresenter
from tests import test_app02_analyzer_workspace_product as product


class CloseLifecycleTests(unittest.TestCase):
    def wait(self, close, phase):
        deadline = time.monotonic() + 3
        while close.poll().phase != phase:
            self.assertLess(time.monotonic(), deadline, close.state)
            time.sleep(.001)

    def test_error_does_not_skip_independent_owner_and_retry_skips_successes(self):
        first = Mock(side_effect=[RuntimeError("owner failed"), None])
        second = Mock()
        prepare = Mock(return_value=(("first", first), ("second", second)))
        close = CloseLifecycle(prepare)
        close.request()
        self.wait(close, "failed")
        self.assertIn("first: owner failed", close.state.detail)
        second.assert_called_once()
        close.request()
        self.wait(close, "complete")
        self.assertEqual(first.call_count, 2)
        second.assert_called_once()
        prepare.assert_called_once()
        close.request()
        self.assertEqual(first.call_count, 2)

    def test_timeout_is_owned_not_terminal_and_repeated_request_does_not_duplicate(self):
        release = threading.Event()
        operation = Mock(side_effect=lambda: release.wait(3))
        close = CloseLifecycle(lambda: (("slow", operation),), timeout_s=.01)
        try:
            close.request()
            self.wait(close, "timeout")
            self.assertEqual(close.request().phase, "timeout")
            operation.assert_called_once()
            self.assertEqual(close._completed, set())
        finally:
            release.set()
            self.wait(close, "complete")

    def test_invalid_prepare_is_not_cached_and_submit_failure_is_retryable(self):
        operation = Mock()
        prepare = Mock(side_effect=[(("same", operation), ("same", operation)), (("ok", operation),)])
        close = CloseLifecycle(prepare)
        self.assertEqual(close.request().phase, "failed")
        with patch("sdr_monitor.ui.v2.shell.close_lifecycle.ThreadPoolExecutor", side_effect=RuntimeError("pool")):
            self.assertEqual(close.request().phase, "failed")
        operation.assert_not_called()
        close.request()
        self.wait(close, "complete")
        operation.assert_called_once()
        self.assertEqual(prepare.call_count, 2)

    def test_empty_and_invalid_deadline(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CloseLifecycle(lambda: (), timeout_s=timeout)
        close = CloseLifecycle(lambda: ())
        self.assertEqual(close.request().phase, "complete")
        self.assertIsNone(close._worker)

    def test_acknowledged_callback_and_preparer_release_while_next_owner_is_pending(self):
        calls = []
        entered, release = threading.Event(), threading.Event()

        class Owner:
            def finish(self):
                calls.append("first")

        class Plan:
            def __init__(self, operation):
                self.operation = operation

            def prepare(self):
                return (("first", self.operation), ("slow", slow))

        def slow():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")

        owner = Owner()
        plan = Plan(owner.finish)
        owner_ref, plan_ref = weakref.ref(owner), weakref.ref(plan)
        close = CloseLifecycle(plan.prepare, timeout_s=.02)
        del owner, plan
        try:
            close.request()
            deadline = time.monotonic() + 3
            while not entered.is_set():
                close.poll()
                self.assertLess(time.monotonic(), deadline, close.state)
                time.sleep(.001)
            self.wait(close, "timeout")
            self.assertTrue(entered.is_set())
            self.assertEqual(close._completed, {"first"})
            self.assertIsNone(plan_ref(), "valid cached plan no longer needs its factory owner")
            self.assertIsNone(owner_ref(), "acknowledged callback must not retain completed owner")
        finally:
            release.set()
            self.wait(close, "complete")
        self.assertEqual(calls, ["first"])
        self.assertEqual(close._tasks, ())
        self.assertEqual(close.request().phase, "complete")

    def test_failed_callback_remains_owned_until_retry_acknowledges(self):
        calls = []

        class Owner:
            def finish(self):
                calls.append("attempt")
                if len(calls) == 1:
                    raise RuntimeError("retry me")

        def create():
            owner = Owner()
            return CloseLifecycle(lambda: (("owner", owner.finish),)), weakref.ref(owner)

        close, reference = create()
        close.request()
        self.wait(close, "failed")
        self.assertIsNotNone(reference())
        self.assertIs(close._tasks[0][1].__self__, reference())
        close.request()
        self.wait(close, "complete")
        self.assertEqual(close._tasks, ())
        self.assertIsNone(close._prepare)
        # The first driver's exception traceback can still own its `self`
        # frame until normal GC. We release lifecycle callbacks, not traceback
        # frames owned by concurrent.futures; no forced-GC deadline assertion.
        self.assertEqual(calls, ["attempt", "attempt"])


class ProductCloseTests(unittest.TestCase):
    # Reuse only the actual build_v2_shell fixture, not its test methods.
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    wait = product.AnalyzerWorkspaceProductTests.wait
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def test_slow_cleanup_keeps_qt_alive_and_timeout_visible_without_releasing_owner(self):
        close = self.composition.close_lifecycle
        close.timeout_s = .03
        gui_thread = threading.get_ident()
        release, entered = threading.Event(), threading.Event()
        workers = []
        original = self.presenter._use_cases.shutdown

        def slow(timeout):
            workers.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")
            original(timeout)

        pulses = []
        timer = QTimer()
        timer.setInterval(2)
        timer.timeout.connect(lambda: pulses.append(1))
        timer.start()
        try:
            with patch.object(self.presenter._use_cases, "shutdown", side_effect=slow) as cleanup:
                start = time.monotonic()
                self.assertFalse(self.shell.close())
                self.assertLess(time.monotonic() - start, .5)
                self.wait(lambda: entered.is_set() and close.state.phase == "timeout" and len(pulses) > 5)
                self.assertTrue(self.shell.isVisible())
                self.assertTrue(self.shell._status_bar.isVisible())
                self.assertFalse(self.shell._root.isEnabled())
                self.assertNotEqual(workers, [gui_thread])
                self.assertEqual(len(workers), 1)
                self.assertFalse(self.shell.close())
                cleanup.assert_called_once()
                self.assertFalse(self.composition._is_shutdown)
                self.assertFalse(self.presenter._poll_timer.isActive())
                release.set()
                self.wait(lambda: self.shell._is_closed)
                self.assertTrue(self.composition._is_shutdown)
        finally:
            release.set()
            timer.stop()

    def test_failure_keeps_window_and_explicit_retry_only_repeats_failed_owner(self):
        original = self.presenter._use_cases.shutdown
        attempts = []

        def fail_once(timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("cleanup proof")
            original(timeout)

        calibration = self.composition._calibration_presenter
        with patch.object(self.presenter._use_cases, "shutdown", side_effect=fail_once), \
             patch.object(calibration, "finish_shutdown", wraps=calibration.finish_shutdown) as other:
            self.shell.close()
            self.wait(lambda: self.composition.close_lifecycle.state.phase == "failed")
            self.assertTrue(self.shell.isVisible())
            self.assertTrue(self.shell._status_bar.isVisible())
            self.assertFalse(self.shell._close_timer.isActive())
            self.assertFalse(self.page.visualization.spectrum_scene._graphics_terminal_released)
            self.assertFalse(self.page.visualization.waterfall_pane._graphics_terminal_released)
            other.assert_called_once()
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)
            self.assertEqual(len(attempts), 2)
            other.assert_called_once()

    def test_active_rx_requires_explicit_stop_and_does_not_start_cleanup(self):
        self.select_and_apply()
        self.page.primary.click()
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        with patch.object(self.presenter, "prepare_shutdown", wraps=self.presenter.prepare_shutdown) as prepare:
            self.assertFalse(self.shell.close())
            prepare.assert_not_called()
            self.assertTrue(self.live.is_running())
            self.assertTrue(self.shell._root.isEnabled())
            self.assertTrue(self.shell._status_bar.isVisible())
            self.assertEqual(self.composition.close_lifecycle.state.phase, "idle")
            self.page.primary.click()
            self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)
            prepare.assert_called_once()

    def test_unopened_deferred_owners_stay_unconstructed(self):
        models = (self.composition.diagnostics_view_model, self.composition.replay_view_model)
        self.assertTrue(all(model._presenter is None for model in models))
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        self.assertTrue(all(model._presenter is None for model in models))
        self.wait(lambda: not any(t.name.startswith("sdr-close") for t in threading.enumerate()))

    def test_opened_deferred_owners_are_quiesced_and_failed_replay_can_retry(self):
        diagnostics = self.composition.diagnostics_view_model
        replay = self.composition.replay_view_model
        diag_cases, replay_cases = Mock(), Mock()
        diag_presenter = DiagnosticsPresenter(diag_cases)
        replay_presenter = ReplayPresenter(replay_cases)
        diagnostics._presenter_factory = lambda: diag_presenter
        replay._presenter_factory = lambda: replay_presenter
        self.assertTrue(diagnostics.load())
        self.assertIs(replay._ready_presenter(), replay_presenter)
        replay_cases.shutdown.side_effect = [RuntimeError("replay cleanup"), None]
        self.shell.close()
        self.wait(lambda: self.composition.close_lifecycle.state.phase == "failed")
        self.assertTrue(diag_presenter._closed)
        self.assertTrue(replay_presenter._closed)
        self.assertFalse(replay_presenter._shutdown_complete)
        self.assertTrue(diagnostics._disposed)
        self.assertTrue(replay._disposed)
        diag_cases.shutdown.assert_called_once()
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        diag_cases.shutdown.assert_called_once()
        self.assertEqual(replay_cases.shutdown.call_count, 2)
        self.assertTrue(replay_presenter._shutdown_complete)

    def test_terminal_ack_retires_pyqtgraph_axes_before_qt_module_shutdown(self):
        spectrum = self.page.visualization.spectrum_scene
        waterfall = self.page.visualization.waterfall_pane
        self.assertIsNotNone(spectrum.plot_item.ctrlMenu)
        self.assertIsNotNone(waterfall.plot_item.ctrlMenu)
        self.assertIsNotNone(spectrum.plot_item.getAxis("left").label)
        self.assertIsNotNone(waterfall.plot_item.getAxis("left").label)
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        self.assertTrue(spectrum._graphics_terminal_released)
        self.assertTrue(waterfall._graphics_terminal_released)
        self.assertIsNone(spectrum.plot_item.ctrlMenu)
        self.assertIsNone(waterfall.plot_item.ctrlMenu)
        self.assertIsNone(spectrum.plot_item.axes)
        self.assertIsNone(waterfall.plot_item.axes)
        self.assertIsNone(waterfall._linked_frequency_source)
        self.assertIsNone(waterfall._source_x_range_callback)
        self.assertIsNone(waterfall._waterfall_x_range_callback)
        spectrum.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()

    def test_terminal_graphics_cleanup_retry_after_waterfall_release(self):
        spectrum = self.page.visualization.spectrum_scene
        waterfall = self.page.visualization.waterfall_pane
        release = spectrum.release_graphics_after_shutdown
        calls = []

        def fail_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("injected spectrum graphics release failure")
            release()

        with patch.object(spectrum, "release_graphics_after_shutdown", side_effect=fail_once):
            self.assertFalse(self.shell.close())
            self.wait(lambda: waterfall._graphics_terminal_released and len(calls) == 1)
            self.assertFalse(self.shell._is_closed)
            self.assertFalse(self.page._terminal_released)
            self.assertFalse(spectrum._graphics_terminal_released)
            self.assertFalse(self.page.visualization.isVisible())
            self.assertIsNone(waterfall._linked_frequency_source)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)

        self.assertEqual(calls, [1, 1])
        self.assertTrue(self.page._terminal_released)
        self.assertTrue(spectrum._graphics_terminal_released)
        self.assertTrue(waterfall._graphics_terminal_released)

    def test_terminal_graphics_cleanup_resumes_partial_plotitem_close(self):
        spectrum = self.page.visualization.spectrum_scene
        waterfall = self.page.visualization.waterfall_pane
        axis = spectrum.plot_item.getAxis("left")
        original_close = axis.close
        calls = []

        def fail_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("injected failure inside PlotItem.close")
            original_close()

        with patch.object(axis, "close", side_effect=fail_once):
            self.assertFalse(self.shell.close())
            self.wait(lambda: spectrum.plot_item.ctrlMenu is None and not self.shell._is_closed)
            self.assertFalse(self.page.visualization.isVisible())
            self.assertIsNotNone(spectrum.plot_item.axes)
            self.assertIsNotNone(spectrum.plot_item.vb)
            self.assertFalse(spectrum._graphics_terminal_released)
            self.assertTrue(waterfall._graphics_terminal_released)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)

        self.assertEqual(calls, [1])
        self.assertTrue(spectrum._graphics_terminal_released)
        self.assertIsNone(spectrum.plot_item.axes)
        self.assertIsNone(spectrum.plot_item.vb)
        self.assertIsNone(axis.label)
        self.assertIsNone(axis.scene())

    def test_multiple_async_ports_are_requested_before_polling_next(self):
        calls = []
        extra = CloseLifecycle(lambda: (("extra", lambda: calls.append("extra")),))
        self.shell._context = replace(self.shell._context, close_ports=(
            *self.shell._context.close_ports,
            ClosePort("extra", lambda: True, lambda: None, extra.request, extra.poll),
        ))
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        self.assertEqual(calls, ["extra"])
        self.assertEqual(extra.state.phase, "complete")


if __name__ == "__main__":
    unittest.main()
