"""Measured clock ordering + actual service converters. Mock only, no SDK/RX."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from enum import Enum
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.analytical_ready import (
    DetectorReadyReceipt, ReadyClockBracket, ReadyClockMapping, ReadyHostBounds,
)
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.identity import ConfigurationGeneration, FrameSequence, SessionId, SourceId
from sdr_monitor.domain.live import LiveSessionState, LiveSnapshot, LiveSpectrumFrame
from sdr_monitor.domain.rtl_live import RtlLiveRequest
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_ready_bridge import NativeReadyBridge
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from tests.test_app07_rtl_product_route import _FrameControl
from tests.test_s15_live_rx_bridge import _FakeNative, _make_frame


class Clock(Enum):
    NativeSteady = 1


class State(Enum):
    Monotonic = 1
    Regressed = 2


def native_with_clock(values=(100, 200)):
    native = _FakeNative()
    native.ANALYTICAL_READY_CONTRACT_VERSION = 1
    native.AnalyticalReadyClock = Clock
    native.AnalyticalReadyClockState = State
    native.analytical_ready_clock_ns = Mock(side_effect=values)
    return native


def receipt(*, ready=150, sequence=1, producer=7, generation=5, state=State.Monotonic):
    return SimpleNamespace(producer_instance_id=producer, offer_sequence=sequence,
        config_generation=generation, ready_native_ns=ready, clock=Clock.NativeSteady,
        clock_state=state)


def bridge(native=None, host=(10_000, 10_010, 11_000, 11_020), *, begin=True):
    native = native if native is not None else native_with_clock()
    host_clock = Mock(side_effect=host)
    value = NativeReadyBridge(native, host_clock=host_clock)
    if begin:
        value.begin()
        value.sample()
    return value, native, host_clock


def frame(ref=None, **kwargs):
    value = _make_frame(1, source_id=kwargs.pop("source_id", "chain"), config_generation=5, **kwargs)
    value.analytical_ready = ref
    return value


def convert(value, raw, **changes):
    args = dict(source_id=SourceId("chain"), config_generation=ConfigurationGeneration(5),
        receiver_id="RX2", acquisition_epoch=9, session_id=SessionId("session"))
    args.update(changes)
    return value.convert(raw, **args)


class NativeReadyClockTests(unittest.TestCase):
    def test_offset_and_rate_are_not_guessed_or_rf_timestamps_replaced(self):
        value, native, host = bridge()
        raw = frame(receipt())
        mapped = convert(value, raw)
        self.assertEqual(mapped.ready_native_ns, 150)
        self.assertEqual(mapped.mapping, ReadyClockMapping.BOUNDED)
        self.assertEqual((mapped.host_bounds.earliest_host_ns, mapped.host_bounds.latest_host_ns,
                          mapped.host_bounds.uncertainty_ns), (10_000, 11_020, 1020))
        self.assertEqual((mapped.source_id, mapped.receiver_id, mapped.acquisition_epoch,
                          mapped.session_id, mapped.config_generation), ("chain", "RX2", 9, "session", 5))
        self.assertEqual(raw.timestamp_ns, 1_000_000_001)
        self.assertEqual((native.analytical_ready_clock_ns.call_count, host.call_count), (2, 4))
        for _ in range(20):
            self.assertEqual(convert(value, raw), mapped)
        self.assertEqual((native.analytical_ready_clock_ns.call_count, host.call_count), (2, 4))
        with self.assertRaises(FrozenInstanceError):
            mapped.ready_native_ns = 0

    def test_no_extrapolation_quantized_equalities_or_history_upgrade(self):
        value, _, _ = bridge()
        for ready in (0, 99, 100, 200, 201, 500):
            mapped = convert(value, frame(receipt(ready=ready)))
            self.assertEqual(mapped.mapping, ReadyClockMapping.OUTSIDE_SAMPLES)
            self.assertIsNone(mapped.host_bounds)
        self.assertIsNone(convert(value, frame()))
        self.assertIsNone(convert(NativeReadyBridge(object()), frame()))
        with self.assertRaisesRegex(ValueError, "protocol"):
            convert(NativeReadyBridge(object()), frame(receipt()))

    def test_regression_and_probe_error_remain_unknown_without_clipping(self):
        for native_values, host_values in (
                ((200, 100), (10_000, 10_010, 11_000, 11_020)),
                ((100, 200), (10_000, 10_010, 9000, 9010)),
                ((100, 200), (10_000, 9999, 11_000, 11_020))):
            with self.subTest(native=native_values, host=host_values):
                value, native, _ = bridge(native_with_clock(native_values), host_values)
                mapped = convert(value, frame(receipt()))
                self.assertEqual(mapped.mapping, ReadyClockMapping.PROBE_REGRESSED)
                self.assertIsNone(mapped.host_bounds)
                before = native.analytical_ready_clock_ns.call_count
                value.sample()
                self.assertEqual(native.analytical_ready_clock_ns.call_count, before)  # latched
        value, _, _ = bridge(native_with_clock((100, RuntimeError("probe"))))
        self.assertEqual(convert(value, frame(receipt())).mapping, ReadyClockMapping.PROBE_FAILED)
        value, _, _ = bridge()
        mapped = convert(value, frame(receipt(ready=-17, state=State.Regressed)))
        self.assertEqual((mapped.ready_native_ns, mapped.mapping), (-17, ReadyClockMapping.PRODUCER_REGRESSED))
        self.assertIsNone(mapped.host_bounds)

    def test_start_reset_does_not_retimestamp_old_or_reuse_mapping(self):
        value, _, _ = bridge(native_with_clock((100, 200, 300, 400)),
            (10_000, 10_010, 11_000, 11_020, 12_000, 12_010, 13_000, 13_020))
        old = convert(value, frame(receipt()))
        value.begin()
        value.sample()
        stale = convert(value, frame(receipt()))
        new = convert(value, frame(receipt(ready=350, sequence=2)))
        self.assertEqual(stale.mapping, ReadyClockMapping.OUTSIDE_SAMPLES)
        self.assertIsNone(stale.host_bounds)
        self.assertEqual(stale.ready_native_ns, old.ready_native_ns)
        self.assertEqual(new.mapping, ReadyClockMapping.BOUNDED)
        self.assertEqual(new.adapter_clock_scope_id, old.adapter_clock_scope_id)

    def test_bounded_scalar_history_no_frames_or_arrays_retained(self):
        native = native_with_clock(range(100, 10_100, 100))
        value, _, _ = bridge(native, tuple(range(10_000, 10_200)), begin=False)
        for _ in range(100):
            value.sample()
        self.assertEqual(len(value._samples), value.SAMPLE_CAPACITY)
        self.assertTrue(all(isinstance(sample, ReadyClockBracket) for sample in value._samples))
        self.assertEqual(convert(value, frame(receipt())).mapping, ReadyClockMapping.OUTSIDE_SAMPLES)
        newer = convert(value, frame(receipt(ready=9950)))
        self.assertEqual(newer.mapping, ReadyClockMapping.BOUNDED)
        other, _, _ = bridge()
        self.assertNotEqual(newer.adapter_clock_scope_id, convert(other, frame(receipt())).adapter_clock_scope_id)

    def test_foreign_source_generation_clock_or_scalar_never_admitted(self):
        value, _, _ = bridge()
        raw = frame(receipt())
        for changes in ({"source_id": "route-not-chain"}, {"config_generation": 6}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                convert(value, raw, **changes)
        for name, invalid in (("producer_instance_id", 0), ("offer_sequence", True),
                ("config_generation", 6), ("ready_native_ns", float("nan")), ("clock", "native")):
            bad = receipt()
            setattr(bad, name, invalid)
            with self.subTest(name=name), self.assertRaises((ValueError, TypeError)):
                convert(value, frame(bad))

    def test_typed_bounds_refuse_invalid_order_outside_and_bool(self):
        for args in ((True, 1, 2), (100, 3, 2)):
            with self.assertRaises(ValueError):
                ReadyClockBracket(*args)
        with self.assertRaises(ValueError):
            ReadyHostBounds(ReadyClockBracket(200, 0, 1), ReadyClockBracket(100, 2, 3))
        value, _, _ = bridge()
        ref = convert(value, frame(receipt()))
        self.assertIsInstance(ref, DetectorReadyReceipt)
        for changes in ({"ready_native_ns": 200}, {"mapping": "bounded"}, {"acquisition_epoch": True},
                        {"source_id": " chain "}, {"session_id": " session "}, {"receiver_id": " RX2 "},
                        {"mapping": ReadyClockMapping.OUTSIDE_SAMPLES}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(ref, **changes)


class ActualReadyAdapterTests(unittest.TestCase):
    def context(self, **kwargs):
        return LiveSnapshot(ConfigurationGeneration(5), FrameSequence(0), LiveSessionState.RUNNING,
            acquisition_epoch=9, clock_domain="host_steady_ns", session_id=SessionId("session"), **kwargs)

    def test_owner_construction_and_cached_read_are_clock_and_sdk_inert(self):
        native = native_with_clock()
        owners = (NativeLiveSessionService(native),
            HackrfAnalyzerService(native, Mock(), Mock(), Mock(), Mock()),
            RtlAnalyzerService(native, Mock(), Mock(), Mock()))
        for owner in owners:
            cached_read = owner.latest_snapshot if isinstance(owner, NativeLiveSessionService) else owner.current_snapshot
            for _ in range(10):
                cached_read()
        native.analytical_ready_clock_ns.assert_not_called()

    def test_pluto_actual_converter_preserves_typed_chain_and_sidecar(self):
        value, native, _ = bridge()
        owner = NativeLiveSessionService(native)
        owner._ready_bridge = value
        raw = frame(receipt())
        mapped = owner._convert_spectrum(raw, self.context(), "chain", receiver_id="RX2")
        self.assertEqual((mapped.source_id, mapped.receiver_id, mapped.detector_ready.receiver_id),
                         ("chain", "RX2", "RX2"))
        self.assertEqual(mapped.detector_ready.ready_native_ns, 150)
        self.assertEqual(mapped.timestamp_ns, raw.timestamp_ns)
        self.assertEqual(mapped.native_quality_flags, raw.quality_flags)
        self.assertIs(replace(mapped, values=mapped.values).detector_ready, mapped.detector_ready)
        for changes in ({"source_id": "other"}, {"config_generation": 6},
                        {"receiver_id": "RX1"}, {"acquisition_epoch": 10}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "differs"):
                replace(mapped, **changes)
        self.assertIsNone(owner._convert_spectrum(frame(), self.context()).detector_ready)

    def test_hackrf_actual_converter_preserves_native_ready_not_poll_time(self):
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph
        fixture = graph()
        binding = fixture.provider.value.binding_for_source("source-hackrf")
        choice = AnalyzerSourceChoice(binding, fixture.provider.runtime, "mock", "mock")
        value, native, _ = bridge()
        owner = HackrfAnalyzerService(native, Mock(), Mock(), Mock(), Mock())
        owner._ready_bridge = value
        request = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20,
            source_id=SourceId("source-hackrf"), configuration_generation=5)
        context = self.context(hackrf_request=request, source_choice=choice, selection_revision=1)
        raw = frame(receipt(), source_id="source-hackrf")
        mapped = owner._convert(raw, context)
        self.assertIsInstance(mapped, LiveSpectrumFrame)
        self.assertEqual((mapped.detector_ready.source_id, mapped.detector_ready.receiver_id,
                          mapped.detector_ready.acquisition_epoch), ("source-hackrf", None, 9))
        self.assertEqual(mapped.detector_ready.mapping, ReadyClockMapping.BOUNDED)
        self.assertIsNone(owner._convert(frame(source_id="source-hackrf"), context).detector_ready)

    def test_rtl_actual_converter_uses_exact_receipt_generation_and_profile(self):
        from tests.test_app07_rtl_manual_gain import fixture
        choice = fixture()[3].selected
        value, native, _ = bridge()
        owner = RtlAnalyzerService(native, Mock(), Mock(), Mock())
        owner._ready_bridge = value
        request = RtlLiveRequest(100_000_000, 2_400_000, detector="peak", source_id=choice.device_id,
                                configuration_generation=5)
        context = self.context(rtl_request=request, source_choice=choice, selection_revision=3)
        control = _FrameControl(request.center_frequency_hz, request.sample_rate_hz, 5)
        raw = control.make_frame(1, source_id=choice.device_id)
        raw.analytical_ready = receipt()
        mapped = owner._convert(raw, context)
        self.assertEqual((mapped.detector_ready.ready_native_ns, mapped.detector_ready.session_id), (150, "session"))
        raw.analytical_ready = receipt(generation=6)
        with self.assertRaisesRegex(ValueError, "generation"):
            owner._convert(raw, context)

    def test_rtl_start_binds_provision_module_not_general_catalog_module(self):
        from sdr_monitor.domain.rtl_live import RtlConfigurationPatch
        from tests.test_app07_rtl_manual_gain import fixture, request
        native, _provider, _inventory, selection, _exclusion, owner = fixture()
        native.ANALYTICAL_READY_CONTRACT_VERSION = 1
        native.AnalyticalReadyClock = Clock
        native.AnalyticalReadyClockState = State
        native.analytical_ready_clock_ns = Mock(return_value=100)
        owner._native = SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=True, RTLSDR_RX_CONTROL_CONTRACT_VERSION=1)
        owner.stage(RtlConfigurationPatch(request(), selection.revision, owner.current_snapshot().generation))
        with patch("sdr_monitor.services.rtl_analyzer.threading.Thread") as thread:
            thread.return_value.ident = None
            started = owner.start()
            self.assertEqual(started.state, LiveSessionState.RUNNING)
            self.assertIs(owner._ready_bridge._native, native)
            self.assertEqual(native.analytical_ready_clock_ns.call_count, 1)
            owner.stop()

    def test_hackrf_existing_poll_path_and_cached_reads_do_not_restamp(self):
        import time
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph, stage
        fixture = graph()
        native = fixture.native
        native.ANALYTICAL_READY_CONTRACT_VERSION = 1
        native.AnalyticalReadyClock = Clock
        native.AnalyticalReadyClockState = State
        native.analytical_ready_clock_ns = Mock(side_effect=range(100, 100_100, 100))
        fixture.factory.change = lambda raw: setattr(raw, "analytical_ready",
            receipt(ready=native.analytical_ready_clock_ns.call_count * 100 + 50,
                    sequence=raw.frame_sequence, generation=raw.config_generation))
        try:
            fixture.application.discover()
            fixture.application.select_device("source-hackrf")
            stage(fixture)
            fixture.application.start()
            deadline = time.monotonic() + 2.
            while fixture.hackrf.current_snapshot().spectrum is None:
                if time.monotonic() > deadline:
                    self.fail("bounded mock ready publication timeout")
                time.sleep(.001)
            fixture.application.stop()
            saved = fixture.hackrf.current_snapshot().spectrum.detector_ready
            calls = native.analytical_ready_clock_ns.call_count
            for _ in range(50):
                self.assertIs(fixture.hackrf.current_snapshot().spectrum.detector_ready, saved)
            self.assertEqual(native.analytical_ready_clock_ns.call_count, calls)
            self.assertEqual(saved.mapping, ReadyClockMapping.BOUNDED)
            self.assertEqual(saved.source_id, "source-hackrf")
        finally:
            fixture.application.shutdown()


if __name__ == "__main__":
    unittest.main()
