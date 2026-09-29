"""APP-07 pane admission through the current AD936x Live application graph."""

from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.live import LiveConfiguration, LiveSessionState
from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
)
from sdr_monitor.domain.recording import RecordingOptions
from sdr_monitor.services.ad936x_rtbw_pane_owner import Ad936xRtbwPaneOwner
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_shared_capture_schedule import pane, profile
from tests.test_native_live_discovery import _FakeEngine, _FakeNative
from tests.test_s15_live_rx_bridge import _make_frame, _wait_for


class _ReadbackEngine(_FakeEngine):
    def __init__(self, uri: str, timeout_ms: int, *, center_offset_hz: float = 0.0) -> None:
        super().__init__(uri, timeout_ms)
        self.frames: list[object] = []
        self.center_offset_hz = center_offset_hz

    def configure(self, config):
        super().configure(config)
        device = config.args[0].args
        self.applied = SimpleNamespace(
            active_backend=SimpleNamespace(name="CPU"),
            center_frequency_hz=device[2] + self.center_offset_hz, sample_rate_hz=device[3],
            analog_bandwidth_hz=device[4], manual_gain_db=device[6],
            config_generation=self.configure_calls,
        )
        return self.applied

    def poll_spectrum_frames(self, max_items: int) -> list[object]:
        del max_items
        return [self.frames.pop(0)] if self.frames else []


class _ReadbackNative(_FakeNative):
    def __init__(self, uri: str = "ip:pluto.local", *, no_readback: bool = False,
                 center_offset_hz: float = 0.0) -> None:
        super().__init__()
        self.uri = uri
        self.no_readback = no_readback
        self.center_offset_hz = center_offset_hz

    def scan_pluto_contexts(self, filter_value: str) -> tuple[object, ...]:
        assert filter_value == "usb,ip"
        return (SimpleNamespace(uri=self.uri, description=self.uri),)

    def probe_pluto_context(self, uri: str, timeout_ms: int) -> object:
        assert uri == self.uri and timeout_ms == 3000
        return SimpleNamespace(model="Analog Devices PlutoSDR", firmware="v0.38")

    def PlutoFixedBandEngine(self, uri: str, timeout_ms: int) -> _FakeEngine:
        engine = (_FakeEngine(uri, timeout_ms) if self.no_readback
                  else _ReadbackEngine(uri, timeout_ms,
                                       center_offset_hz=self.center_offset_hz))
        self.engines.append(engine)
        return engine


class _UnusedSweep:
    def start(self, request) -> None:
        raise AssertionError("RTBW pane cannot start Sweep")

    def stop(self) -> None:
        raise AssertionError("RTBW pane cannot stop Sweep")


class Ad936xRtbwPaneOwnerTests(unittest.TestCase):
    def graph(self, uri: str = "ip:pluto.local", *, no_readback: bool = False,
              center_offset_hz: float = 0.0):
        native = _ReadbackNative(uri, no_readback=no_readback,
                                 center_offset_hz=center_offset_hz)
        service = NativeLiveSessionService(native)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(LiveConfiguration(
            center_hz=104e6, sample_rate_hz=20e6,
            analog_bandwidth_hz=10e6, gain_db=20,
            fft_size=4096, detector="sample", persistence_enabled=False,
            persistence_mode="disabled",
        ))
        analyzer = AnalyzerSessionApplicationService(service, _UnusedSweep(),
                                                     start_live=service.start_admitted)
        live = LiveSessionApplicationService(service, analyzer=analyzer)
        return native, service, live, device.device_id

    @staticmethod
    def session(live: LiveSessionApplicationService, source_id: str, *,
                mode: CaptureMeasurementMode = CaptureMeasurementMode.RTBW,
                owner_source_id: str | None = None,
                selection: ReceiverChainSelection = ReceiverChainSelection.RX1):
        resource_id, endpoint_id = "physical-a", "rx-a"
        group = AcquisitionGroup("group-a", resource_id,
                                 (ReceiverEndpoint(endpoint_id, source_id, resource_id,
                                                   selection),))
        requested = (
            pane("low", endpoint_id, 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
            pane("high", endpoint_id, 140e6, 148e6, ReceiverBindingMode.TIME_SLICED),
        )
        capture_profile = replace(profile(8e6, mode), sample_rate_hz=20e6,
                                  analog_bandwidth_hz=10e6,
                                  detector="sample")
        schedule = compile_pane_schedule((group,), requested, {"capture": capture_profile})
        owner = Ad936xRtbwPaneOwner(live, physical_stream_resource_id=resource_id,
                                    source_id=owner_source_id or source_id,
                                    receiver_endpoint_id=endpoint_id)
        leases = ReceiverLeaseManager()
        return PaneResourceSession(schedule, (group,), {resource_id: owner}, leases), leases

    def test_same_native_application_owner_retunes_two_rtbw_ranges_with_new_epoch(self) -> None:
        native, service, live, source_id = self.graph()
        session, leases = self.session(live, source_id)
        self.assertEqual(len(native.engines), 0)
        self.assertFalse(session.preview()[0].recording_conflict)
        session.apply()
        first = session.start_resource("physical-a")
        first_epoch = service.latest_snapshot().acquisition_epoch
        first_center = service.latest_snapshot().applied.applied.center_hz
        self.assertIn(first_center, (104e6, 144e6))
        self.assertEqual(len(native.engines), 1)
        second = session.advance_resource("physical-a")
        self.assertNotEqual(first.host_activation_serial, second.host_activation_serial)
        self.assertGreater(service.latest_snapshot().acquisition_epoch, first_epoch)
        self.assertEqual({first_center, service.latest_snapshot().applied.applied.center_hz},
                         {104e6, 144e6})
        self.assertEqual(len(native.engines), 2)
        self.assertEqual(native.engines[0].disconnect_calls, 1)
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(native.engines[1].disconnect_calls, 1)
        self.assertEqual(leases.active_resource_count, 0)
        self.assertIs(service.latest_snapshot().state, LiveSessionState.CONNECTED)

    def test_sweep_plan_refused_before_leasing_or_native_start(self) -> None:
        native, _service, live, source_id = self.graph()
        with self.assertRaisesRegex(PaneResourceError, "refuses a scheduled capture job"):
            self.session(live, source_id, mode=CaptureMeasurementMode.SWEEP)
        self.assertEqual(native.engines, [])

    def test_rx2_plan_refused_before_leasing_or_native_start(self) -> None:
        native, _service, live, source_id = self.graph()
        with self.assertRaisesRegex(PaneResourceError, "producer receiver identity"):
            self.session(live, source_id, selection=ReceiverChainSelection.RX2)
        self.assertEqual(native.engines, [])

    def test_native_poller_bundle_reaches_only_its_current_pane(self) -> None:
        native, service, live, source_id = self.graph()
        session, _leases = self.session(live, source_id)
        session.apply()
        session.start_resource("physical-a")
        center = service.latest_snapshot().applied.applied.center_hz
        expected_pane = "low" if center == 104e6 else "high"
        frame = _make_frame(1, center_hz=center, sample_rate_hz=20e6,
                            source_id=source_id, config_generation=1)
        frame.frequencies_hz = center + (np.arange(4096) - 2048) * (20e6 / 4096)
        frame.values[-1] = np.nan
        native.engines[0].frames.append(frame)
        self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
        deliveries = session.poll_resource("physical-a")
        self.assertEqual(tuple(item.pane_id for item in deliveries), (expected_pane,))
        self.assertTrue(np.isnan(deliveries[0].bundle.values[-1]))
        self.assertEqual(deliveries[0].bundle.identity.source_id, source_id)
        self.assertEqual(session.stop_all(), ())

    def test_two_application_graphs_keep_independent_rx_owners(self) -> None:
        native_a, service_a, live_a, source_a = self.graph("ip:pluto-a.local")
        native_b, service_b, live_b, source_b = self.graph("ip:pluto-b.local")
        self.assertNotEqual(source_a, source_b)
        groups = (
            AcquisitionGroup("group-a", "physical-a", (ReceiverEndpoint(
                "rx-a", source_a, "physical-a", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("group-b", "physical-b", (ReceiverEndpoint(
                "rx-b", source_b, "physical-b", ReceiverChainSelection.RX1),)),
        )
        requested = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("second", "rx-b", 140e6, 148e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        capture_profile = replace(profile(8e6, CaptureMeasurementMode.RTBW),
                                  sample_rate_hz=20e6, analog_bandwidth_hz=10e6,
                                  detector="sample")
        schedule = compile_pane_schedule(groups, requested, {"capture": capture_profile})
        owners = {
            "physical-a": Ad936xRtbwPaneOwner(live_a, physical_stream_resource_id="physical-a",
                                                source_id=source_a, receiver_endpoint_id="rx-a"),
            "physical-b": Ad936xRtbwPaneOwner(live_b, physical_stream_resource_id="physical-b",
                                                source_id=source_b, receiver_endpoint_id="rx-b"),
        }
        leases = ReceiverLeaseManager()
        session = PaneResourceSession(schedule, groups, owners, leases, source_identity_keys={
            source_a: stable_identity_key("fake physical A"),
            source_b: stable_identity_key("fake physical B"),
        })
        session.apply()
        session.start_resource("physical-a")
        session.start_resource("physical-b")
        self.assertEqual(leases.active_resource_count, 2)
        self.assertTrue(service_a.is_running() and service_b.is_running())
        self.assertEqual(session.stop_selected("first"), ("first",))
        self.assertFalse(service_a.is_running())
        self.assertTrue(service_b.is_running())
        self.assertEqual(native_a.engines[0].disconnect_calls, 1)
        self.assertEqual(native_b.engines[0].disconnect_calls, 0)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(native_b.engines[0].disconnect_calls, 1)
        self.assertEqual(leases.active_resource_count, 0)

    def test_armed_recording_blocks_plan_before_receiver_lease(self) -> None:
        native, service, live, source_id = self.graph()
        session, leases = self.session(live, source_id)
        service.arm_native_recording(RecordingOptions("app07-test-not-written.iq"))
        self.assertTrue(session.preview()[0].recording_conflict)
        with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
            session.apply()
        self.assertEqual(leases.active_resource_count, 0)
        self.assertEqual(native.engines, [])
        service.stop_native_recording()

    def test_recording_armed_during_live_blocks_retune_without_stopping_rx(self) -> None:
        native, service, live, source_id = self.graph()
        session, _leases = self.session(live, source_id)
        session.apply()
        session.start_resource("physical-a")
        service.arm_native_recording(RecordingOptions("app07-test-not-written.iq"))
        with self.assertRaisesRegex(PaneResourceError, "recording blocks"):
            session.advance_resource("physical-a")
        self.assertTrue(service.is_running())
        self.assertEqual(len(native.engines), 1)
        self.assertEqual(native.engines[0].disconnect_calls, 0)
        self.assertEqual(session.stop_all(), ())
        service.stop_native_recording()

    def test_ordinary_live_controls_cannot_bypass_active_pane_claim(self) -> None:
        native, service, live, source_id = self.graph()
        session, leases = self.session(live, source_id)
        session.apply()
        session.start_resource("physical-a")
        requested = service.latest_snapshot().applied.applied
        for command in (
            lambda: live.discover(),
            lambda: live.select_device(source_id),
            lambda: live.apply_configuration(requested),
            lambda: live.start(),
            lambda: live.stop(),
        ):
            with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
                command()
        self.assertEqual(native.engines[0].disconnect_calls, 0)
        session.advance_resource("physical-a")
        with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
            live.stop()
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)
        self.assertEqual(native.engines[-1].disconnect_calls, 1)
        self.assertIs(live.apply_configuration(requested).state, LiveSessionState.CONNECTED)

    def test_pane_claim_releases_after_failed_start_and_explicit_stop(self) -> None:
        native, _service, live, source_id = self.graph(no_readback=True)
        session, leases = self.session(live, source_id)
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("physical-a")
        with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
            live.stop()
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)
        self.assertEqual(native.engines[0].disconnect_calls, 1)
        self.assertIs(live.current_snapshot().state, LiveSessionState.CONNECTED)

    def test_missing_rf_readback_retains_owner_for_explicit_stop(self) -> None:
        native, service, live, source_id = self.graph(no_readback=True)
        session, leases = self.session(live, source_id)
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("physical-a")
        self.assertEqual(len(native.engines), 1)
        self.assertTrue(service.is_running())
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(native.engines[0].disconnect_calls, 1)

    def test_two_hertz_lo_quantization_is_admitted_with_coverage(self) -> None:
        _native, service, live, source_id = self.graph(center_offset_hz=-2.0)
        session, _leases = self.session(live, source_id)
        session.apply()
        session.start_resource("physical-a")
        center = service.latest_snapshot().applied.applied.center_hz
        self.assertIn(center, (104e6 - 2.0, 144e6 - 2.0))
        self.assertEqual(session.stop_all(), ())

    def test_large_lo_readback_shift_refuses_and_retains_owner(self) -> None:
        native, service, live, source_id = self.graph(center_offset_hz=10_000.0)
        session, leases = self.session(live, source_id)
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("physical-a")
        self.assertTrue(service.is_running())
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(native.engines[0].disconnect_calls, 1)

    def test_source_mismatch_refuses_before_lease_or_native_start(self) -> None:
        native, _service, live, source_id = self.graph()
        with self.assertRaisesRegex(PaneResourceError, "producer receiver identity"):
            self.session(live, source_id, owner_source_id="other-source")
        self.assertEqual(native.engines, [])

    def test_recording_transaction_collision_refuses_without_waiting_or_rx(self) -> None:
        native, _service, live, source_id = self.graph()
        session, leases = self.session(live, source_id)
        session.apply()
        entered, release = Event(), Event()

        def hold_transaction() -> None:
            with live.pane_control_transaction():
                entered.set()
                release.wait(2)

        worker = Thread(target=hold_transaction)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(PaneResourceError, "transaction failed"):
                session.start_resource("physical-a")
            self.assertEqual(native.engines, [])
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)


if __name__ == "__main__":
    unittest.main()
