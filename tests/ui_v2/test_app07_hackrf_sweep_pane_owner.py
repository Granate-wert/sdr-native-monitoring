"""APP-07 HackRF RTBW/Sweep share one product RX owner, with no real SDK."""

from __future__ import annotations

import time
import unittest

from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, CaptureMeasurementMode, HackrfRtbwPaneProfile,
    HackrfSweepPaneProfile, compile_pane_schedule,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
    SweepPaneRequest,
)
from sdr_monitor.services.hackrf_pane_owner import HackrfPaneOwner
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.ui_v2.test_app06_hackrf_common_analyzer import graph
from tests.ui_v2.test_app06_hackrf_sweep_common_analyzer import FakeHackrfSweep


_COST = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)


class HackrfSweepPaneOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.g = graph()
        self.g.application.discover()
        self.g.application.select_device("source-hackrf")
        self.fake = FakeHackrfSweep()
        self.router = AnalyzerSweepRouter(self.g.analyzer._sweep, self.g.sources,
                                          None, self.fake)
        self.g.analyzer._sweep = self.router
        self.session: PaneResourceSession | None = None
        self.resource, self.endpoint = "hf:physical", "hf:rx1"

    def tearDown(self) -> None:
        if self.session is not None:
            self.assertEqual(self.session.stop_all(), ())
        self.g.application.shutdown()

    def make_session(self, *, mixed: bool = False, sweep_available: bool = True):
        selection = self.g.application.current_source_selection()
        assert selection is not None and selection.selected is not None
        request = HackrfSweepRequest(selection.selected, selection.revision,
                                     100_000_000, 220_000_000, 4096, 16, 20, 50)
        profiles = {"sweep": HackrfSweepPaneProfile(request, _COST)}
        sweep_profile = profiles["sweep"]
        group = AcquisitionGroup("hf:group", self.resource, (
            ReceiverEndpoint(self.endpoint, "source-hackrf", self.resource,
                             ReceiverChainSelection.RX1),))
        if mixed:
            live = HackrfLiveRequest(104e6, 20e6, 15_000_000, 16, 20,
                                     fft_size=4096, hop_size=2048, detector="peak",
                                     source_id="source-hackrf")
            profiles["rtbw"] = HackrfRtbwPaneProfile(live, 10e6, _COST)
            requests = (
                SweepPaneRequest("a_rtbw", self.endpoint, 100e6, 108e6,
                                 profile_id="rtbw",
                                 requested_binding_mode=ReceiverBindingMode.TIME_SLICED),
                SweepPaneRequest("b_sweep", self.endpoint,
                                 sweep_profile.pane_crop_start_hz,
                                 sweep_profile.pane_crop_stop_hz,
                                 profile_id="sweep",
                                 requested_binding_mode=ReceiverBindingMode.TIME_SLICED),
            )
        else:
            requests = (SweepPaneRequest("wide", self.endpoint,
                                         sweep_profile.pane_crop_start_hz,
                                         sweep_profile.pane_crop_stop_hz,
                                         profile_id="sweep",
                                         requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL),)
        schedule = compile_pane_schedule((group,), requests, profiles)
        owner = HackrfPaneOwner(self.g.application, self.router,
            physical_stream_resource_id=self.resource, source_id="source-hackrf",
            receiver_endpoint_id=self.endpoint, sweep_available=sweep_available)
        leases = ReceiverLeaseManager()
        self.session = PaneResourceSession(schedule, (group,),
                                           {self.resource: owner}, leases)
        return self.session, leases

    def wait(self, predicate) -> None:
        deadline = time.monotonic() + 2.0
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded fake HackRF publication timeout")
            time.sleep(.001)

    def test_sweep_progress_terminal_one_resource_and_explicit_stop(self) -> None:
        session, leases = self.make_session()
        self.assertEqual(self.fake.events, [])
        session.apply()
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(self.fake.events, [])  # Apply is not Start.
        session.start_resource(self.resource)
        self.assertEqual(self.fake.events[0], "start")
        self.assertEqual(self.fake.request.epoch, 1)
        self.assertIs(self.fake.request.source, self.fake.selection.selected)
        progress = session.poll_resource(self.resource)
        terminal = session.poll_resource(self.resource)
        self.assertEqual((len(progress), len(terminal)), (1, 1))
        self.assertEqual((progress[0].bundle.mode, terminal[0].bundle.mode),
                         ("sweep", "sweep"))
        self.assertEqual(progress[0].bundle.identity.unit, "dBFS/bin")
        self.assertEqual(progress[0].bundle.publication_kind.value, "sweep_progress")
        self.assertEqual(terminal[0].bundle.publication_kind.value, "sweep_complete")
        self.assertGreater(progress[0].crop.start_hz, self.fake.request.start_hz)
        self.assertLess(terminal[0].crop.stop_hz, self.fake.request.stop_hz)
        self.assertEqual(session.poll_resource(self.resource), ())
        self.assertEqual(session.stop_all(), ())
        self.assertIn("stop", self.fake.events)
        self.assertEqual(leases.active_resource_count, 0)

    def test_one_rx_switches_rtbw_to_sweep_only_after_stop(self) -> None:
        session, leases = self.make_session(mixed=True)
        session.apply()
        first = session.start_resource(self.resource)
        self.assertEqual(first.capture_id, f"{self.resource}:capture:0")
        self.assertEqual(len(self.g.factory.controls), 1)
        self.wait(lambda: self.g.application.current_snapshot().spectrum is not None)
        first_frames = session.poll_resource(self.resource)
        self.assertEqual(first_frames[0].bundle.mode, "rtbw")
        second = session.advance_resource(self.resource)
        self.assertNotEqual(first.host_activation_serial, second.host_activation_serial)
        self.assertEqual(self.g.factory.controls[0].stops, [5000])
        self.assertEqual(self.fake.events[0], "start")
        self.assertEqual(session.poll_resource(self.resource)[0].bundle.mode, "sweep")
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertIn("stop", self.fake.events)
        self.assertEqual(leases.active_resource_count, 0)

    def test_missing_sweep_port_refuses_before_lease_or_native_start(self) -> None:
        with self.assertRaisesRegex(PaneResourceError, "refuses a scheduled capture job"):
            self.make_session(sweep_available=False)
        self.assertEqual(self.fake.events, [])
        self.assertEqual(self.g.factory.controls, [])

    def test_sweep_admission_has_no_fabricated_hop(self) -> None:
        session, _leases = self.make_session()
        session.apply()
        session.start_resource(self.resource)
        admission = session._runtimes[self.resource].admission
        assert admission is not None
        self.assertIs(admission.mode, CaptureMeasurementMode.SWEEP)
        self.assertEqual(admission.sample_rate_hz, 20e6)
        self.assertEqual(admission.fft_size, 4096)
        self.assertIsNone(admission.hop_size)


if __name__ == "__main__":
    unittest.main()
