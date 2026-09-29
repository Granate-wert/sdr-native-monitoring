"""Three existing family graphs + Empty, with fake SDK/serial rather than RF."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace

import numpy as np

from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.live import LiveConfiguration
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, HackrfRtbwPaneProfile, PaneCaptureProfile,
    PaneLayoutSlot, TinySaTracePaneProfile, compile_pane_layout,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
    SpectrumTraceEndpoint,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.ad936x_rtbw_pane_owner import Ad936xRtbwPaneOwner
from sdr_monitor.services.hackrf_rtbw_pane_owner import HackrfRtbwPaneOwner
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.tinysa_trace_pane_owner import TinySaTracePaneOwner

from tests.test_app07_ad936x_rtbw_pane_owner import _ReadbackNative, _UnusedSweep
from tests.test_app07_shared_capture_schedule import pane
from tests.test_s15_live_rx_bridge import _make_frame
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_graph
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_graph


_COST = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)


def _ad936x_graph():
    native = _ReadbackNative("ip:pluto-app07.local")
    service = NativeLiveSessionService(native)
    device = service.discover_devices()[0]
    service.select_device(device.device_id)
    service.apply_configuration(LiveConfiguration(
        center_hz=104e6, sample_rate_hz=20e6, analog_bandwidth_hz=10e6,
        gain_db=20, fft_size=4096, detector="sample",
        persistence_enabled=False, persistence_mode="disabled"))
    analyzer = AnalyzerSessionApplicationService(service, _UnusedSweep(),
                                                 start_live=service.start_admitted)
    return native, service, LiveSessionApplicationService(service, analyzer=analyzer), device.device_id


class ThreeConcreteOwnerTests(unittest.TestCase):
    @staticmethod
    def wait(predicate) -> None:
        deadline = time.monotonic() + 4.0
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("bounded three-family publication timeout")
            time.sleep(0.001)

    def test_parallel_three_family_owners_and_empty_fourth_slot(self) -> None:
        ad_native, ad_service, ad_live, ad_source = _ad936x_graph()
        hf = hackrf_graph()
        ts = tinysa_graph()
        hf.application.discover()
        hf.application.select_device("source-hackrf")
        ts_choice = ts.application.discover()[0]
        ts.application.select_device(ts_choice.device_id)
        ts_selection = ts.sources.current()
        assert ts_selection.selected is not None
        groups = (
            AcquisitionGroup("ad:group", "ad:physical", (
                ReceiverEndpoint("ad:rx1", ad_source, "ad:physical", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("hf:group", "hf:physical", (
                ReceiverEndpoint("hf:rx1", "source-hackrf", "hf:physical", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("ts:group", "ts:physical", (
                SpectrumTraceEndpoint("ts:trace", ts_selection.selected.device_id, "ts:physical"),)),
        )
        slots = (
            PaneLayoutSlot(1, pane("ad-pane", "ad:rx1", 100e6, 108e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(2, pane("hf-pane", "hf:rx1", 140e6, 148e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(3, pane("ts-pane", "ts:trace", 200e6, 210e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(4),
        )
        profiles = {
            "ad": PaneCaptureProfile(20e6, 10e6, "manual", 20.0,
                                     4096, 2048, "hann", "sample", None, 10e6, _COST),
            "hf": HackrfRtbwPaneProfile(
                HackrfLiveRequest(144e6, 20e6, 15_000_000, 16, 20,
                                  fft_size=4096, hop_size=2048, detector="peak",
                                  source_id="source-hackrf"), 10e6, _COST),
            "ts": TinySaTracePaneProfile(
                3, 11e6, "ultra-default", _COST,
                request_template=TinySaSweepRequest(ts_selection.selected,
                    ts_selection.revision, 200_000_000, 210_000_000, 3)),
        }
        # Each occupied slot names its own typed profile; Empty has no request.
        slots = tuple(PaneLayoutSlot(slot.number, replace(slot.request,
            profile_id={1: "ad", 2: "hf", 3: "ts"}[slot.number]))
            if slot.request is not None else slot for slot in slots)
        layout = compile_pane_layout(slots, groups, profiles)
        self.assertEqual(layout.empty_slots, (4,))
        assert layout.schedule is not None
        owners = {
            "ad:physical": Ad936xRtbwPaneOwner(ad_live,
                physical_stream_resource_id="ad:physical", source_id=ad_source,
                receiver_endpoint_id="ad:rx1"),
            "hf:physical": HackrfRtbwPaneOwner(hf.application,
                physical_stream_resource_id="hf:physical", source_id="source-hackrf",
                receiver_endpoint_id="hf:rx1"),
            "ts:physical": TinySaTracePaneOwner(ts.application, ts.instrument,
                physical_stream_resource_id="ts:physical", source_id=ts_selection.selected.device_id,
                trace_endpoint_id="ts:trace"),
        }
        leases = ReceiverLeaseManager(max_active_resources=4)
        session = PaneResourceSession(layout.schedule, groups, owners, leases)
        try:
            session.apply()
            for resource in ("ad:physical", "hf:physical", "ts:physical"):
                session.start_resource(resource)
            self.assertEqual(leases.active_resource_count, 3)
            self.assertTrue(ad_service.is_running() and hf.hackrf.is_running())
            self.assertTrue(ts.instrument.stop_required)
            center = ad_service.latest_snapshot().applied.applied.center_hz
            frame = _make_frame(1, center_hz=center, sample_rate_hz=20e6,
                                source_id=ad_source, config_generation=1)
            frame.frequencies_hz = center + (np.arange(4096) - 2048) * (20e6 / 4096)
            ad_native.engines[0].frames.append(frame)
            self.wait(lambda: ad_service.latest_snapshot().spectrum is not None)
            self.wait(lambda: hf.hackrf.current_snapshot().spectrum is not None)
            self.wait(lambda: ts.instrument.poll_latest().line is not None)
            deliveries = {
                resource: session.poll_resource(resource)
                for resource in ("ad:physical", "hf:physical", "ts:physical")
            }
            self.assertEqual(tuple(item.pane_id for item in deliveries["ad:physical"]), ("ad-pane",))
            self.assertEqual(tuple(item.pane_id for item in deliveries["hf:physical"]), ("hf-pane",))
            self.assertEqual(tuple(item.pane_id for item in deliveries["ts:physical"]), ("ts-pane",))
            self.assertEqual(deliveries["ad:physical"][0].bundle.unit, "dBFS/bin")
            self.assertEqual(deliveries["hf:physical"][0].bundle.unit, "dBFS/bin")
            self.assertEqual(deliveries["ts:physical"][0].bundle.unit, "dBm")
            self.assertEqual(session.stop_selected("ts-pane"), ("ts-pane",))
            self.assertEqual(leases.active_resource_count, 2)
            self.assertTrue(ad_service.is_running() and hf.hackrf.is_running())
            self.assertFalse(ts.instrument.stop_required)
        finally:
            self.assertEqual(session.stop_all(), ())
            self.assertEqual(leases.active_resource_count, 0)
            ad_live.shutdown()
            hf.application.shutdown()
            ts.application.shutdown()


if __name__ == "__main__":
    unittest.main()
