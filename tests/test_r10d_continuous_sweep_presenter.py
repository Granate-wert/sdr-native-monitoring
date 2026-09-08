"""R10-D2 presentation tests: line cadence and actual render cadence stay separate."""

from __future__ import annotations

import os
import time
import threading
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import SweepLineFrame, SweepLineState
from sdr_monitor.services.native_continuous_sweep import (
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
)
from sdr_monitor.services.native_continuous_sweep_factory import (
    ContinuousSweepPlanRequest,
    NativeLiveContinuousSweepDisplayService,
)
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter


_APP = QApplication.instance() or QApplication([])


def _line() -> SweepLineFrame:
    return SweepLineFrame(
        sequence=1,
        epoch=1,
        completed_at_ns=1,
        source_id="test",
        state=SweepLineState.COMPLETE,
        frequencies_hz=np.array([100.0, 101.0]),
        values_db=np.array([-90.0, -89.0], dtype=np.float32),
        quality_flags=np.array([0, 0], dtype=np.uint16),
        source_segment_indices=np.array([0, 1], dtype=np.int32),
        missing_segment_indices=(),
        segment_config_generations=((0, 1), (1, 2)),
        gap_reasons=(),
        unit="dBFS/bin",
    )


class _Service:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.snapshot = ContinuousSweepDisplaySnapshot(
            _line(),
            ContinuousSweepDisplayMetrics(completed_line_lps=123.0, completed_lines=123),
        )

    def start(self, _config: object) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        return self.snapshot


class R10DContinuousSweepPresenterTests(unittest.TestCase):
    def test_snapshot_line_lps_and_render_fps_have_independent_sources(self) -> None:
        service = _Service()
        presenter = ContinuousSweepPresenter(service, max_poll_hz=60.0)
        lines: list[SweepLineFrame] = []
        metrics: list[ContinuousSweepDisplayMetrics] = []
        presenter.line_ready.connect(lines.append)
        presenter.metrics_ready.connect(metrics.append)

        presenter._emit_snapshot(service.snapshot)
        presenter.record_completed_render(5.0, now_s=10.0)
        presenter.record_completed_render(7.0, now_s=10.5)
        render = presenter.render_metrics(now_s=10.75)

        self.assertEqual(lines, [service.snapshot.line])
        self.assertEqual(metrics[0].completed_line_lps, 123.0)
        self.assertEqual(render.render_fps, 2.0)
        self.assertEqual(render.budget_misses, 0)
        presenter.shutdown()
        self.assertTrue(service.closed)

    def test_presenter_keeps_live_composition_deferred_until_start(self) -> None:
        """The presenter accepts the Live port without acquiring a lease early."""

        events: list[str] = []

        class Display:
            def start(self, config: object) -> None:
                events.append(f"display-start:{config}")

            def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
                events.append("display-final-poll")
                return ContinuousSweepDisplaySnapshot(
                    line=None,
                    metrics=ContinuousSweepDisplayMetrics(),
                )

            def stop(self) -> None:
                events.append("display-stop")

            def close(self) -> None:
                events.append("display-close")

        class Factory:
            @classmethod
            def from_native_live(cls, _live: object) -> "Factory":
                events.append("lease")
                return cls()

            def build(self, request: ContinuousSweepPlanRequest) -> str:
                events.append(f"build:{request.start_hz}")
                return "native-config"

            def create_display_service(self) -> Display:
                events.append("display")
                return Display()

            def close(self) -> None:
                events.append("release")

        service = NativeLiveContinuousSweepDisplayService(object())
        presenter = ContinuousSweepPresenter(service, max_poll_hz=60.0)
        self.assertEqual(events, [])
        with patch(
            "sdr_monitor.services.native_continuous_sweep_factory.NativeContinuousSweepPlanFactory",
            Factory,
        ):
            presenter.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))
            presenter.shutdown()
        self.assertEqual(
            events,
            [
                "lease",
                "build:2400000000.0",
                "display",
                "display-start:native-config",
                "display-stop",
                "display-final-poll",
                "display-close",
                "release",
            ],
        )

    def test_stop_delivers_final_bundle_before_running_false(self) -> None:
        service = _Service()
        presenter = ContinuousSweepPresenter(service)
        events = []
        presenter.analyzer_ready.connect(lambda frame: events.append(("frame", frame)))
        presenter.running_changed.connect(lambda running: events.append(("running", running)))
        presenter.start(object())
        presenter.stop()
        deadline = time.monotonic() + 2
        while presenter._stop_future is not None and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.001)
        self.assertEqual([event[0] for event in events], ["running", "frame", "running"])
        self.assertIs(events[1][1].spectrum, service.snapshot.line)
        self.assertFalse(events[-1][1])
        presenter.shutdown()
        self.assertEqual(len(events), 3)

    def test_stop_is_nonblocking_and_rejects_start_until_qt_completion(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        class BlockingService(_Service):
            starts = 0

            def start(self, config):
                self.starts += 1
                super().start(config)

            def stop(self):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test stop barrier timed out")
                super().stop()

        service = BlockingService()
        presenter = ContinuousSweepPresenter(service)
        delivered_threads = []
        presenter.analyzer_ready.connect(lambda _: delivered_threads.append(threading.get_ident()))
        presenter.start(object())
        try:
            presenter.stop()
            self.assertTrue(entered.wait(1))
            self.assertFalse(release.is_set())
            presenter.start(object())
            self.assertEqual(service.starts, 1)
            _APP.processEvents()
            self.assertIsNotNone(presenter._stop_future)
            release.set()
            deadline = time.monotonic() + 2
            while presenter._stop_future is not None and time.monotonic() < deadline:
                _APP.processEvents()
                time.sleep(0.001)
            self.assertIsNone(presenter._stop_future)
            self.assertEqual(delivered_threads, [threading.get_ident()])
            presenter.start(object())
            self.assertEqual(service.starts, 2)
        finally:
            release.set()
            presenter.shutdown()

    def test_failed_stop_latches_restart_and_reports_once(self) -> None:
        class FailedService(_Service):
            starts = 0

            def start(self, config):
                self.starts += 1
                super().start(config)

            def stop(self):
                raise RuntimeError("stop ownership unresolved")

        service = FailedService()
        presenter = ContinuousSweepPresenter(service)
        errors = []
        presenter.task_failed.connect(errors.append)
        presenter.start(object())
        presenter.stop()
        deadline = time.monotonic() + 2
        while presenter.is_stopping and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.001)
        self.assertFalse(presenter.is_stopping)
        self.assertEqual(errors, ["stop ownership unresolved"])
        presenter.start(object())
        self.assertEqual(service.starts, 1)
        presenter.shutdown()
        _APP.processEvents()
        self.assertEqual(errors, ["stop ownership unresolved"])

    def test_poll_failure_stops_acquisition_before_reporting_stopped(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        class Service(_Service):
            polls = 0
            starts = 0

            def start(self, config):
                self.starts += 1
                super().start(config)

            def poll_latest(self):
                self.polls += 1
                if self.polls == 1:
                    raise RuntimeError("publication failed")
                return self.snapshot

            def stop(self):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test stop barrier timed out")
                super().stop()

        service = Service()
        presenter = ContinuousSweepPresenter(service)
        running = []
        errors = []
        presenter.running_changed.connect(running.append)
        presenter.task_failed.connect(errors.append)
        presenter.start(object())
        try:
            presenter._poll()
            self.assertTrue(entered.wait(1))
            self.assertEqual(running, [True])
            self.assertTrue(presenter.is_stopping)
            self.assertEqual(errors, ["publication failed"])
            presenter.start(object())
            presenter._poll()
            self.assertEqual(service.starts, 1)
            self.assertEqual(service.polls, 1)
            release.set()
            deadline = time.monotonic() + 2
            while presenter.is_stopping and time.monotonic() < deadline:
                _APP.processEvents()
                time.sleep(0.001)
            self.assertEqual(running, [True, False])
            self.assertFalse(service.started)
            presenter.start(object())
            self.assertEqual(service.starts, 1)
        finally:
            release.set()
            presenter.shutdown()


if __name__ == "__main__":
    unittest.main()
