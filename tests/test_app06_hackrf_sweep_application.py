"""Qt-free application admission tests for bounded HackRF Sweep requests."""

import unittest

from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerSessionApplicationService
from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceCapabilityBinding, DeviceFamily
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.live import LiveSnapshot, LiveSessionState, LiveAdmissionRejected


def choice(family: DeviceFamily = DeviceFamily.HACKRF) -> AnalyzerSourceChoice:
    binding = DeviceCapabilityBinding("radio-1", family, "adapter-1")
    return AnalyzerSourceChoice(binding, None, "test source", "USB")


def request(source: AnalyzerSourceChoice, revision: int = 7) -> HackrfSweepRequest:
    return HackrfSweepRequest(source, revision, 100_000_000, 120_000_000, 2048, 16, 20, 10)


class Sources:
    def __init__(self, selection: AnalyzerSourceSelection) -> None:
        self.selection = selection

    def current(self) -> AnalyzerSourceSelection:
        return self.selection


class Port:
    def __init__(self) -> None:
        self.events = []
        self.request = None
        self.selection = None
        self.snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())

    def start(self, value, selected) -> None:
        self.events.append("start")
        self.request, self.selection = value, selected

    def stop(self) -> None:
        self.events.append("stop")

    def poll_latest(self):
        self.events.append("poll")
        return self.snapshot


class NativePort(Port):
    def start(self, value) -> None:
        self.events.append("native-start")


class Live:
    def is_running(self) -> bool:
        return False

    def start(self) -> LiveSnapshot:
        return LiveSnapshot(generation=1, sequence=1, state=LiveSessionState.RUNNING)

    def stop(self) -> LiveSnapshot:
        return LiveSnapshot(generation=1, sequence=2, state=LiveSessionState.CONNECTED)


class HackrfSweepApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = choice()
        self.selection = AnalyzerSourceSelection(7, (self.source,), self.source.device_id)
        self.sources = Sources(self.selection)
        self.native = NativePort()
        self.hackrf = Port()
        self.router = AnalyzerSweepRouter(self.native, self.sources, None, self.hackrf)

    def test_wrong_family_is_refused_before_any_port_effect(self) -> None:
        wrong = choice(DeviceFamily.AD936X)
        router = AnalyzerSweepRouter(self.native, Sources(
            AnalyzerSourceSelection(7, (wrong,), wrong.device_id)), None, self.hackrf)
        with self.assertRaises(LiveAdmissionRejected):
            router.start(request(wrong))
        self.assertEqual(self.hackrf.events, [])
        self.assertEqual(self.native.events, [])

    def test_stale_revision_and_different_choice_object_are_refused(self) -> None:
        with self.assertRaises(LiveAdmissionRejected):
            self.router.start(request(self.source, revision=6))
        other = choice()
        with self.assertRaises(LiveAdmissionRejected):
            self.router.start(request(other))
        self.assertEqual(self.hackrf.events, [])

    def test_uncomposed_hackrf_port_refuses_without_falling_through_to_native(self) -> None:
        router = AnalyzerSweepRouter(self.native, self.sources, None)
        with self.assertRaises(LiveAdmissionRejected):
            router.start(request(self.source))
        self.assertEqual(self.native.events, [])

    def test_pre_effect_port_refusal_does_not_capture_cleanup_authority(self) -> None:
        class RefusingPort(Port):
            def start(self, value, selected) -> None:
                raise LiveAdmissionRejected("paired contract refused before RX")

        refusing = RefusingPort()
        router = AnalyzerSweepRouter(self.native, self.sources, None, refusing)
        with self.assertRaises(LiveAdmissionRejected):
            router.start(request(self.source))
        router.stop()
        self.assertEqual(refusing.events, [])
        self.assertEqual(self.native.events, [])

    def test_dispatch_captures_owner_and_stop_preserves_terminal_poll_owner(self) -> None:
        value = request(self.source)
        self.router.start(value)
        self.assertEqual(self.hackrf.events, ["start"])
        self.assertIs(self.hackrf.request, value)
        self.assertIs(self.hackrf.selection, self.selection)
        self.sources.selection = AnalyzerSourceSelection(8)
        self.router.stop()
        self.assertEqual(self.hackrf.events, ["start", "stop"])
        self.assertIs(self.router.poll_latest(), self.hackrf.snapshot)
        self.assertEqual(self.hackrf.events, ["start", "stop", "poll"])
        self.assertEqual(self.native.events, [])

    def test_shared_session_accepts_and_epochs_typed_hackrf_request(self) -> None:
        class CapturingSweep:
            received = None

            def start(self, value) -> None:
                self.received = value

            def stop(self) -> None:
                pass

        sweep = CapturingSweep()
        owner = AnalyzerSessionApplicationService(Live(), sweep)
        owner.select_mode(AnalyzerMode.SWEEP)
        supplied = request(self.source)
        state = owner.start(supplied)
        # Native HackRF Sweep requires a positive epoch/config generation.
        self.assertEqual(state.sweep_epoch, 1)
        self.assertEqual(sweep.received.epoch, 1)
        self.assertIs(sweep.received.source, supplied.source)
        owner.stop()

    def test_domain_bounds_are_strict_without_claiming_tuning_range_support(self) -> None:
        for kwargs in ({"fft_size": 512}, {"lna_gain": 9}, {"vga_gain": 63},
                       {"preview_rate_hz": 101}, {"stop_hz": 120_000_001}):
            values = dict(source=self.source, selection_revision=7, start_hz=100_000_000,
                          stop_hz=120_000_000, fft_size=2048, lna_gain=16,
                          vga_gain=20, preview_rate_hz=10)
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                HackrfSweepRequest(**values)

    def test_whole_mhz_analysis_stop_retains_explicit_extended_capture_padding(self) -> None:
        # APP-07 extended geometry permits non-20-MHz analysis spans. It does
        # not turn capture padding into measured analysis coverage or grant
        # legacy modules permission to execute the plan.
        supplied = HackrfSweepRequest(self.source, 7, 100_000_000, 121_000_000,
                                       2048, 16, 20, 10)
        self.assertEqual(supplied.stop_hz, 121_000_000)
        self.assertEqual(supplied.hardware_stop_hz, 140_000_000)
        self.assertTrue(supplied.requires_extended_geometry)


if __name__ == "__main__":
    unittest.main()
