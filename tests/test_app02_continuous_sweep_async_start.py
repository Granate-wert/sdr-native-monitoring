"""APP-02 nonblocking continuous-Sweep presenter admission tests."""

from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import (
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
)
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    if not predicate():
        raise AssertionError("Qt condition did not become true")


class _BlockingStartService:
    def __init__(self, *, failure: Exception | None = None, owns_failure: bool = False) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = failure
        self.stop_required = False
        self.owns_failure = owns_failure
        self.stop_calls = 0
        self.close_calls = 0

    def start(self, _request: object) -> None:
        self.entered.set()
        if not self.release.wait(2):
            raise RuntimeError("test Start barrier expired")
        if self.failure is not None:
            self.stop_required = self.owns_failure
            raise self.failure
        self.stop_required = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.stop_required = False

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        return ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())

    def close(self) -> None:
        self.close_calls += 1


class ContinuousSweepAsyncStartTests(unittest.TestCase):
    def test_slow_start_does_not_block_qt_heartbeat(self) -> None:
        service = _BlockingStartService()
        presenter = ContinuousSweepPresenter(service)
        heartbeat: list[bool] = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))

        started_at = time.monotonic()
        presenter.start(object())
        self.assertLess(time.monotonic() - started_at, 0.1)
        self.assertTrue(service.entered.wait(1))
        self.assertTrue(presenter.is_starting)
        _wait_until(lambda: bool(heartbeat))

        service.release.set()
        _wait_until(lambda: not presenter.is_starting)
        self.assertTrue(presenter._timer.isActive())
        presenter.shutdown()

    def test_rejected_admission_does_not_stop_foreign_owner(self) -> None:
        service = _BlockingStartService(failure=RuntimeError("occupied"), owns_failure=False)
        presenter = ContinuousSweepPresenter(service)
        errors: list[str] = []
        presenter.task_failed.connect(errors.append)
        presenter.start(object())
        self.assertTrue(service.entered.wait(1))
        service.release.set()
        _wait_until(lambda: not presenter.is_starting)

        self.assertEqual(errors, ["occupied"])
        self.assertEqual(service.stop_calls, 0)
        self.assertTrue(presenter.can_close())
        presenter.shutdown()

    def test_owned_failed_start_is_cleaned_before_restart_is_allowed(self) -> None:
        service = _BlockingStartService(failure=RuntimeError("partial start"), owns_failure=True)
        presenter = ContinuousSweepPresenter(service)
        presenter.start(object())
        self.assertTrue(service.entered.wait(1))
        service.release.set()
        _wait_until(lambda: not presenter.is_starting and not presenter.is_stopping)

        self.assertEqual(service.stop_calls, 1)
        self.assertFalse(service.stop_required)
        self.assertTrue(presenter.can_close())
        presenter.shutdown()

    def test_shutdown_waits_for_pending_start_then_cleans_owned_session(self) -> None:
        service = _BlockingStartService()
        presenter = ContinuousSweepPresenter(service)
        presenter.start(object())
        self.assertTrue(service.entered.wait(1))

        def release_later() -> None:
            time.sleep(0.05)
            service.release.set()

        releaser = threading.Thread(target=release_later)
        releaser.start()
        started_at = time.monotonic()
        presenter.shutdown()
        releaser.join(timeout=1)

        self.assertGreaterEqual(time.monotonic() - started_at, 0.04)
        self.assertEqual(service.stop_calls, 1)
        self.assertEqual(service.close_calls, 1)
        self.assertTrue(presenter._closed)


if __name__ == "__main__":
    unittest.main()
