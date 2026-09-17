"""Application epoch issuance is separate from UI reset and RF config generation."""
from dataclasses import replace
import unittest

from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService, AnalyzerMode, AnalyzerPhase
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from tests.test_app01_analyzer_session import Live, Sweep


class CapturingSweep(Sweep):
    def __init__(self, events):
        super().__init__(events)
        self.requests = []
        self.fail = False

    def start(self, request):
        self.requests.append(request)
        super().start(request)
        if self.fail:
            raise RuntimeError("partial native start failure")


class SweepEpochPolicyTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.sweep = CapturingSweep(self.events)
        self.live = Live(self.events)
        self.owner = AnalyzerSessionApplicationService(self.live, self.sweep)
        self.request = ContinuousSweepPlanRequest(100e6, 136e6)
        self.owner.select_mode(AnalyzerMode.SWEEP)

    def test_repeated_start_issues_new_epoch_and_keeps_original_draft(self):
        for expected in range(3):
            state = self.owner.start(self.request)
            self.assertEqual(state.sweep_epoch, expected)
            self.assertEqual(self.sweep.requests[-1].epoch, expected)
            self.owner.stop()
            self.assertEqual(self.owner.state.sweep_epoch, expected)
        self.assertEqual(self.request.epoch, 0)
        self.assertEqual(self.events, ["sweep-start", "sweep-stop"] * 3)

    def test_explicit_epoch_is_a_floor_and_failed_start_does_not_recycle_it(self):
        self.sweep.fail = True
        with self.assertRaisesRegex(RuntimeError, "partial native"):
            self.owner.start(replace(self.request, epoch=100))
        self.assertEqual(self.owner.state.sweep_epoch, 100)
        self.assertIs(self.owner.state.phase, AnalyzerPhase.ERROR)
        with self.assertRaises(RuntimeError):
            self.owner.start(self.request)
        self.owner.stop()
        self.sweep.fail = False
        self.assertEqual(self.owner.start(self.request).sweep_epoch, 101)
        self.owner.stop()
        self.assertEqual([r.epoch for r in self.sweep.requests], [100, 101])

    def test_rtbw_switch_is_explicit_and_does_not_reset_sweep_epoch_authority(self):
        self.owner.start(self.request)
        with self.assertRaises(RuntimeError):
            self.owner.select_mode(AnalyzerMode.RTBW)
        self.owner.stop()
        self.owner.select_mode(AnalyzerMode.RTBW)
        self.assertEqual(self.events, ["sweep-start", "sweep-stop"])
        self.assertIsNone(self.owner.start().sweep_epoch)
        self.owner.stop()
        self.owner.select_mode(AnalyzerMode.SWEEP)
        self.assertEqual(self.owner.start(self.request).sweep_epoch, 1)
        self.owner.stop()
        self.assertEqual(self.events, ["sweep-start", "sweep-stop", "rtbw-start", "rtbw-stop",
                                       "sweep-start", "sweep-stop"])

    def test_epoch_overflow_and_invalid_request_never_dispatch(self):
        for epoch in (-1, True, 0.5, 1 << 64):
            with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                replace(self.request, epoch=epoch)
        self.owner.start(replace(self.request, epoch=(1 << 64) - 1))
        self.owner.stop()
        before = self.owner.state
        with self.assertRaises(OverflowError):
            self.owner.start(self.request)
        self.assertEqual(self.owner.state, before)
        self.assertEqual(self.events, ["sweep-start", "sweep-stop"])
