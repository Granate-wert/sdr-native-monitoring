from __future__ import annotations

import unittest
from dataclasses import replace

from sdr_monitor.application.analyzer_continuous_sweep import (
    AnalyzerContinuousSweepApplicationService,
)
from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerPhase, AnalyzerSessionState
from sdr_monitor.domain.analyzer_display import (
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
)
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.live import LiveSessionState, LiveSnapshot
from sdr_monitor.domain.identity import as_configuration_generation, as_frame_sequence


REQUEST = ContinuousSweepPlanRequest(100e6, 136e6)


def _snapshot(
    sequence: int,
    state: LiveSessionState,
    *,
    error: str | None = None,
    stop_required: bool = False,
) -> LiveSnapshot:
    return LiveSnapshot(
        as_configuration_generation(1),
        as_frame_sequence(sequence),
        state,
        error=error,
        stop_required=stop_required,
    )


class _Application:
    def __init__(self) -> None:
        self.analyzer_state: AnalyzerSessionState | None = AnalyzerSessionState()
        self.starts: list[ContinuousSweepPlanRequest] = []
        self.stop_calls = 0
        self.start_error: Exception | None = None
        self.start_error_after_admission = False
        self.stop_results = [
            _snapshot(1, LiveSessionState.CONNECTED, stop_required=False),
        ]

    def start_sweep(self, request: ContinuousSweepPlanRequest) -> AnalyzerSessionState:
        self.starts.append(request)
        operation_id = self.analyzer_state.operation_id + 1 if self.analyzer_state else 1
        if self.start_error is not None:
            if self.start_error_after_admission:
                self.analyzer_state = AnalyzerSessionState(
                    AnalyzerMode.SWEEP, AnalyzerPhase.ERROR, str(self.start_error), operation_id,
                )
            raise self.start_error
        self.analyzer_state = AnalyzerSessionState(AnalyzerMode.SWEEP, AnalyzerPhase.RUNNING,
                                                   operation_id=operation_id)
        return self.analyzer_state

    def stop(self) -> LiveSnapshot:
        self.stop_calls += 1
        result = self.stop_results.pop(0)
        assert self.analyzer_state is not None
        if not result.stop_required:
            self.analyzer_state = replace(self.analyzer_state, phase=AnalyzerPhase.IDLE)
        else:
            self.analyzer_state = replace(self.analyzer_state, phase=AnalyzerPhase.ERROR, error=result.error)
        return result


class _Display:
    def __init__(self) -> None:
        self.snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())
        self.poll_calls = 0

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        self.poll_calls += 1
        return self.snapshot


class AnalyzerContinuousSweepApplicationTests(unittest.TestCase):
    def test_shared_start_publication_stop_and_close_preserve_one_terminal_poll(self):
        application, display = _Application(), _Display()
        facade = AnalyzerContinuousSweepApplicationService(application, display)

        facade.start(REQUEST)
        self.assertEqual(application.starts, [REQUEST])
        self.assertIs(facade.poll_latest(), display.snapshot)
        facade.stop()
        # Presenter owns the one post-Stop terminal/final-gap poll.
        self.assertIs(facade.poll_latest(), display.snapshot)
        facade.close()
        facade.close()

        self.assertEqual(application.stop_calls, 1)
        self.assertEqual(display.poll_calls, 2)

    def test_failed_sweep_start_retains_safe_explicit_cleanup(self):
        application, display = _Application(), _Display()
        application.start_error = RuntimeError("failed")
        application.start_error_after_admission = True
        facade = AnalyzerContinuousSweepApplicationService(application, display)

        with self.assertRaisesRegex(RuntimeError, "failed"):
            facade.start(REQUEST)
        facade.close()

        self.assertEqual(application.stop_calls, 1)
        self.assertEqual(display.poll_calls, 0)

    def test_rejected_attempt_cannot_claim_existing_sweep_operation(self):
        application, display = _Application(), _Display()
        application.analyzer_state = AnalyzerSessionState(AnalyzerMode.SWEEP, AnalyzerPhase.RUNNING,
                                                          operation_id=4)
        application.start_error = RuntimeError("already occupied")
        facade = AnalyzerContinuousSweepApplicationService(application, display)
        with self.assertRaisesRegex(RuntimeError, "occupied"):
            facade.start(REQUEST)
        facade.close()
        self.assertEqual(application.stop_calls, 0)

    def test_old_facade_cannot_stop_a_new_sweep_operation(self):
        application, display = _Application(), _Display()
        facade = AnalyzerContinuousSweepApplicationService(application, display)
        facade.start(REQUEST)
        application.analyzer_state = AnalyzerSessionState(AnalyzerMode.SWEEP, AnalyzerPhase.RUNNING,
                                                          operation_id=2)
        facade.close()
        self.assertEqual(application.stop_calls, 0)

    def test_failed_stop_keeps_cleanup_for_close_retry(self):
        application, display = _Application(), _Display()
        application.stop_results = [
            _snapshot(1, LiveSessionState.ERROR, error="stop failed", stop_required=True),
            _snapshot(2, LiveSessionState.CONNECTED, stop_required=False),
        ]
        facade = AnalyzerContinuousSweepApplicationService(application, display)
        facade.start(REQUEST)

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            facade.stop()
        facade.close()

        self.assertEqual(application.stop_calls, 2)

    def test_late_live_restart_is_never_stopped_by_old_sweep_facade(self):
        application, display = _Application(), _Display()
        facade = AnalyzerContinuousSweepApplicationService(application, display)
        facade.start(REQUEST)
        # A different owner completed Sweep Stop and then admitted RTBW before
        # this facade observed the common state.
        application.analyzer_state = AnalyzerSessionState(AnalyzerMode.RTBW, AnalyzerPhase.RUNNING)

        facade.close()

        self.assertEqual(application.stop_calls, 0)

    def test_rejected_request_during_unrelated_rtbw_does_not_gain_cleanup_ownership(self):
        application, display = _Application(), _Display()
        application.analyzer_state = AnalyzerSessionState(AnalyzerMode.RTBW, AnalyzerPhase.RUNNING)
        application.start_error = RuntimeError("stop active operation")
        facade = AnalyzerContinuousSweepApplicationService(application, display)

        with self.assertRaisesRegex(RuntimeError, "stop active"):
            facade.start(REQUEST)
        facade.close()

        self.assertEqual(application.stop_calls, 0)


if __name__ == "__main__":
    unittest.main()
