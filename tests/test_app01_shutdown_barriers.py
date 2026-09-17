"""Deterministic APP-01 shutdown ordering; fake infrastructure only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.sweep import SweepConfiguration
from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.application.sweep_control import SweepControlApplicationService
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.ui.presenters.sweep_presenter import SweepPresenter
from sdr_monitor.ui.presenters.live_presenter import LivePresenter
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from tests.test_native_live_discovery import _FakeNative
from tests.ui_v2.test_live_product_composition import (
    FakeCalibrationPresenter,
    FakePresenter,
    FakeSweepPresenter,
)
from tests.ui_v2.test_live_view_model import FakeSignal


class NativeShutdownBarrierTests(unittest.TestCase):
    def test_shutdown_cannot_finish_before_admitted_start_is_cleaned(self) -> None:
        service = NativeLiveSessionService(_FakeNative())
        entered, stop_entered, release = threading.Event(), threading.Event(), threading.Event()
        engine = Mock()

        def delayed_start():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test start barrier expired")
            service._engine = engine
            service._snapshot = replace(service._snapshot, state=LiveSessionState.RUNNING)
            return service._snapshot

        def observed_stop() -> None:
            stop_entered.set()
            service.stop_and_wait(1.0)

        with patch.object(service, "_start_unlocked", side_effect=delayed_start), \
                ThreadPoolExecutor(max_workers=2) as pool:
            starting = pool.submit(service.start_admitted)
            self.assertTrue(entered.wait(1))
            stopping = pool.submit(observed_stop)
            self.assertTrue(stop_entered.wait(1))
            try:
                with self.assertRaises(FutureTimeoutError):
                    stopping.result(timeout=0.05)
            finally:
                release.set()
            starting.result(timeout=2)
            stopping.result(timeout=2)

        self.assertIsNone(service._engine)
        self.assertIsNone(service._poller)
        self.assertFalse(service.is_running())
        engine.request_stop.assert_called_once_with()
        engine.join.assert_called_once_with()
        engine.disconnect.assert_called_once_with()


class _AnalyzerPresenter:
    def __init__(self, events: list[str], *, failure: Exception | None = None) -> None:
        self.events = events
        self.failure = failure
        self.analyzer_ready = FakeSignal()
        self.snapshot_ready = FakeSignal()
        self.task_failed = FakeSignal()
        self.starting_changed = FakeSignal()
        self.stopping_changed = FakeSignal()
        self.running_changed = FakeSignal()

    def can_close(self) -> bool:
        return True

    def shutdown(self) -> None:
        self.events.append("analyzer")
        if self.failure is not None:
            error, self.failure = self.failure, None
            raise error


class _OrderedLive(FakePresenter):
    def __init__(self, events: list[str], *, failure: Exception | None = None) -> None:
        super().__init__()
        self.events = events
        self.failure = failure

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.events.append("live")
        if self.failure is not None:
            error, self.failure = self.failure, None
            raise error


class _OrderedSweep(FakeSweepPresenter):
    def __init__(self, events: list[str], *, failure: Exception | None = None) -> None:
        super().__init__()
        self.events = events
        self.failure = failure

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.events.append("bounded-sweep")
        if self.failure is not None:
            error, self.failure = self.failure, None
            raise error


class _OrderedCalibration(FakeCalibrationPresenter):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.events.append("calibration")


class ProductShutdownOrderingTests(unittest.TestCase):
    def test_bounded_sweep_releases_before_analyzer_live_and_failures_do_not_skip_owners(self) -> None:
        events: list[str] = []
        live = _OrderedLive(events)
        bounded = _OrderedSweep(events, failure=RuntimeError("bounded cleanup failed"))
        analyzer = _AnalyzerPresenter(events, failure=RuntimeError("analyzer cleanup failed"))
        calibration = _OrderedCalibration(events)
        composition = compose_v2_live_product(
            live,
            sweep_presenter=bounded,
            analyzer_presenter=analyzer,
            calibration_presenter=calibration,
        )

        with self.assertRaisesRegex(RuntimeError, "bounded cleanup failed"):
            composition.shutdown()

        self.assertEqual(events, ["bounded-sweep", "analyzer", "live", "calibration"])
        self.assertFalse(composition._is_shutdown)
        # Failed cleanup is retryable; all fake owners are deliberately
        # idempotent and the second complete pass becomes terminal.
        composition.shutdown()
        self.assertTrue(composition._is_shutdown)
        self.assertEqual(bounded.shutdown_calls, 2)
        self.assertEqual(live.shutdown_calls, 2)
        self.assertEqual(calibration.shutdown_calls, 2)

    def test_real_bounded_sweep_presenter_shutdown_cancels_worker_and_releases_owner(self) -> None:
        entered, cancelled = threading.Event(), threading.Event()

        class Port:
            close_calls = 0

            def execute(self, configuration, progress):
                entered.set()
                if not cancelled.wait(2):
                    raise RuntimeError("bounded Sweep cancel barrier expired")
                return Mock()

            def cancel(self) -> None:
                cancelled.set()

            def close(self) -> None:
                self.close_calls += 1

            def plan(self, configuration):
                return Mock()

            def export_result(self, result, output_path):
                return output_path

        class IdleLive:
            def is_running(self) -> bool:
                return False

            def start(self):
                return Mock()

            def stop(self):
                return Mock()

        port = Port()
        analyzer = AnalyzerSessionApplicationService(IdleLive(), Mock())
        presenter = SweepPresenter(SweepControlApplicationService(port, analyzer=analyzer))
        presenter.execute(SweepConfiguration())
        self.assertTrue(entered.wait(1))

        presenter.shutdown()

        self.assertTrue(cancelled.is_set())
        self.assertEqual(port.close_calls, 1)
        self.assertEqual(analyzer.state.phase.value, "idle")

    def test_sweep_presenter_retries_failed_cleanup_instead_of_false_success(self) -> None:
        class RetryUseCases:
            calls = 0
            execute = Mock()

            def shutdown(self) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient cleanup failure")

        use_cases = RetryUseCases()
        presenter = SweepPresenter(use_cases)
        with self.assertRaisesRegex(RuntimeError, "transient cleanup failure"):
            presenter.shutdown()
        self.assertFalse(presenter._closed)
        presenter.execute(SweepConfiguration())
        use_cases.execute.assert_not_called()
        presenter.shutdown()
        self.assertTrue(presenter._closed)
        self.assertEqual(use_cases.calls, 2)

    def test_live_presenter_retries_failed_use_case_cleanup(self) -> None:
        class RetryLiveUseCases:
            calls = 0
            start = Mock()

            def shutdown(self, timeout_s: float) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient live cleanup failure")

        use_cases = RetryLiveUseCases()
        presenter = LivePresenter(use_cases)
        with self.assertRaisesRegex(RuntimeError, "transient live cleanup failure"):
            presenter.shutdown()
        self.assertFalse(presenter._closed)
        presenter.start()
        use_cases.start.assert_not_called()
        presenter.shutdown()
        self.assertTrue(presenter._closed)
        self.assertEqual(use_cases.calls, 2)


if __name__ == "__main__":
    unittest.main()
