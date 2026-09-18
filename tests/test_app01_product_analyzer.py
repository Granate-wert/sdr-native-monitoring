"""Actual UI V2 composition over fake infrastructure; no receiver or file I/O."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveSnapshot
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.analyzer_display import (
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
)
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.live_session import InMemoryLiveSessionService, fake_pluto_device
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2_composition import build_v2_shell


def _immutable(values, dtype):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _progress() -> SweepProgressFrame:
    return SweepProgressFrame(
        source_id="fake-sweep",
        sequence=1,
        epoch=7,
        revision=1,
        unit="dBFS/bin",
        frequencies_hz=_immutable((100e6, 101e6), np.float64),
        values_db=_immutable((-70.0, np.nan), np.float32),
        quality_flags=_immutable((0, 1 << 12), np.uint32),
        source_segment_indices=_immutable((0, -1), np.int32),
        acquired_segment_generations=((0, 11),),
        pending_segment_indices=(1,),
    )


class _AtomicFakeLive(InMemoryLiveSessionService):
    def __init__(self, events: list[str]) -> None:
        super().__init__((fake_pluto_device(),))
        self.events = events
        self.stop_and_wait_calls = 0

    def start(self):
        # In-memory composition is explicitly non-hardware. Atomic native
        # admission is covered separately by test_app01_native_live_admission.
        self.events.append("rtbw-start")
        return super().start()

    def stop(self):
        self.events.append("rtbw-stop")
        return super().stop()

    def stop_and_wait(self, timeout_s: float) -> None:
        self.stop_and_wait_calls += 1
        super().stop_and_wait(timeout_s)


class _FakeAnalyzerDisplay:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.running = False
        self.stop_calls = 0
        self.poll_calls = 0
        self._final = False

    def start(self, request: ContinuousSweepPlanRequest) -> None:
        del request
        self.events.append("sweep-start")
        self.running = True
        self._final = False

    def stop(self) -> None:
        self.events.append("sweep-stop")
        self.stop_calls += 1
        self.running = False
        self._final = True

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        self.poll_calls += 1
        if self.running:
            return ContinuousSweepDisplaySnapshot(
                None, ContinuousSweepDisplayMetrics(), progress=_progress(),
            )
        if self._final:
            self._final = False
            return ContinuousSweepDisplaySnapshot(
                None,
                ContinuousSweepDisplayMetrics(
                    gapped_lines=1,
                    terminal_control_gaps=1,
                ),
            )
        raise RuntimeError("fake analyzer display is idle")

    def close(self) -> None:
        # The application facade must not bypass the shared owner with this.
        raise AssertionError("display.close bypassed shared Analyzer lifecycle")


class ProductAnalyzerCompositionTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, predicate, *, timeout_s: float = 2.0) -> None:
        deadline = time.monotonic() + timeout_s
        while not predicate():
            self.app.processEvents()
            if time.monotonic() >= deadline:
                self.fail("timed out waiting for presenter completion")
            time.sleep(0.001)
        self.app.processEvents()

    def test_control_completion_invalidates_queued_running_render(self) -> None:
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.domain import LiveSessionState
        from sdr_monitor.ui.presenters.live_presenter import LivePresenter

        presenter = LivePresenter(LiveSessionApplicationService(InMemoryLiveSessionService()))
        renders = []
        presenter.render_ready.connect(renders.append)
        running = LiveSnapshot(generation=1, sequence=1, state=LiveSessionState.RUNNING)
        stopped = replace(running, state=LiveSessionState.CONNECTED)
        try:
            presenter.offer_snapshot_for_render(running)
            presenter._emit_snapshot(stopped)
            presenter._display_scheduler._flush()
            self.assertEqual(renders, [])
            presenter.offer_snapshot_for_render(stopped)
            presenter._display_scheduler._flush()
            self.assertEqual(renders, [stopped])
        finally:
            presenter.shutdown()

    def test_actual_product_serializes_rtbw_and_progressive_sweep(self) -> None:
        events: list[str] = []
        live = _AtomicFakeLive(events)
        display = _FakeAnalyzerDisplay(events)
        services = SimpleNamespace(
            live_sdr=live,
            analyzer_display=display,
            sweep=Mock(),
            calibration=Mock(),
            diagnostics=Mock(),
            replay=Mock(),
        )
        captured = []

        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            captured.append((composition, args, kwargs))
            return composition

        with patch(
            "sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture,
        ):
            shell = build_v2_shell(services)
        composition, args, kwargs = captured[0]
        live_presenter = args[0]
        analyzer_presenter = kwargs["analyzer_presenter"]
        self.assertIs(composition.analyzer_presenter, analyzer_presenter)
        live_snapshots: list[LiveSnapshot] = []
        live_failures: list[str] = []
        rtbw_frames: list[AnalyzerFrameBundle | None] = []
        analyzer_frames: list[AnalyzerFrameBundle] = []
        analyzer_metrics: list[ContinuousSweepDisplayMetrics] = []
        live_presenter.snapshot_changed.connect(live_snapshots.append)
        live_presenter.task_failed.connect(live_failures.append)
        live_presenter.analyzer_ready.connect(rtbw_frames.append)
        analyzer_presenter.analyzer_ready.connect(analyzer_frames.append)
        analyzer_presenter.metrics_ready.connect(analyzer_metrics.append)

        closed = False
        try:
            # Construction and navigation are presentation-only.
            self.assertEqual(shell.active_workspace_id, "analyzer")
            self.assertNotIn("live", {item.workspace_id for item in composition.context.workspaces})
            self.assertNotIn("sweep", {item.workspace_id for item in composition.context.workspaces})
            for route in ("analyzer", "calibration", "analyzer"):
                shell.select_workspace(route)
                self.app.processEvents()
            self.assertEqual(events, [])

            live_presenter.select_device("fake-pluto-usb")
            self._wait(lambda: len(live_snapshots) >= 1)
            configuration = LiveConfiguration(
                sample_rate_hz=3e6,
                analog_bandwidth_hz=3e6,
                fft_size=1024,
                backend=BackendKind.CPU,
            )
            live_presenter.start_with_configuration(configuration)
            self._wait(lambda: any(snapshot.state.value == "running" for snapshot in live_snapshots))
            self.assertEqual(events, ["rtbw-start"])

            frame = LiveSpectrumFrame(
                sequence=1, timestamp_ns=12, source_id="fake-pluto-usb",
                config_generation=1, center_frequency_hz=configuration.center_hz,
                sample_rate_hz=3e6, fft_size=1024, hop_size=1024,
                frequencies_hz=configuration.center_hz + (np.arange(1024) - 512) * (3e6 / 1024),
                values=np.full(1024, -70.0, dtype=np.float32),
                unit="dBFS/bin", native_quality_flags=1,
            )
            delivered = replace(live.latest_snapshot(), spectrum=frame)
            live._snapshot = delivered
            live_presenter.offer_snapshot_for_render(delivered)
            self._wait(lambda: any(bundle is not None for bundle in rtbw_frames))
            measured = next(bundle for bundle in rtbw_frames if bundle is not None)
            self.assertEqual(measured.mode, "rtbw")
            self.assertIs(measured.spectrum, frame)
            self.assertEqual(measured.spectrum.native_quality_flags, 1)
            self.assertEqual(measured.unit, "dBFS/bin")

            request = ContinuousSweepPlanRequest(
                100e6, 102e6, usable_window_hz=2e6, overlap_hz=0,
            )
            analyzer_presenter.start(request)
            self._wait(lambda: not analyzer_presenter.is_starting)
            self.assertEqual(events, ["rtbw-start"])

            live_presenter.stop()
            self._wait(lambda: events == ["rtbw-start", "rtbw-stop"])
            analyzer_presenter.start(request)
            self._wait(lambda: not analyzer_presenter.is_starting)
            self.assertEqual(events[-1], "sweep-start")
            analyzer_presenter._poll()
            self._wait(lambda: bool(analyzer_frames))
            self.assertEqual(len(analyzer_frames), 1)
            self.assertEqual(analyzer_frames[0].mode, "sweep")

            live_presenter.start()
            self._wait(lambda: bool(live_failures))
            self.assertEqual(events.count("rtbw-start"), 1)
            analyzer_presenter.stop()
            self._wait(lambda: not analyzer_presenter.is_stopping)
            self.assertEqual(display.stop_calls, 1)
            self.assertEqual(analyzer_metrics[-1].terminal_control_gaps, 1)

            live_presenter.start()
            self._wait(lambda: events.count("rtbw-start") == 2)
            live_presenter.stop()
            self._wait(
                lambda: events.count("rtbw-stop") == 2
                and not composition.view_model.state.busy
                and not live.is_running()
                and analyzer_presenter.can_close()
            )
        finally:
            if analyzer_presenter._timer.isActive() and not analyzer_presenter.is_stopping:
                analyzer_presenter.stop()
                self._wait(lambda: not analyzer_presenter.is_stopping)
            if live.is_running():
                live_presenter.stop()
                self._wait(lambda: not live.is_running())
            closed = shell.close()
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()

        self.assertTrue(closed)
        self.assertEqual(display.stop_calls, 1)
        self.assertEqual(live.stop_and_wait_calls, 1)
        services.sweep.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
