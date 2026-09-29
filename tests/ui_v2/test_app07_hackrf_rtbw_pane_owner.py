"""APP-07 same-owner HackRF RTBW pane contract; fake SDK, no physical RX."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, HackrfRtbwPaneProfile, compile_pane_schedule,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
)
from sdr_monitor.services.hackrf_rtbw_pane_owner import HackrfRtbwPaneOwner
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_shared_capture_schedule import pane, profile
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph


_COST = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)


def _session(g, *, wrong_profile: bool = False):
    resource, endpoint = "hackrf:physical", "hackrf:rx1"
    group = AcquisitionGroup("hackrf:group", resource, (
        ReceiverEndpoint(endpoint, "source-hackrf", resource, ReceiverChainSelection.RX1),))
    requests = (
        pane("low", endpoint, 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
        pane("high", endpoint, 140e6, 148e6, ReceiverBindingMode.TIME_SLICED),
    )
    request = HackrfLiveRequest(104e6, 20e6, 15_000_000, 16, 20,
                                fft_size=4096, hop_size=2048, detector="peak",
                                source_id="source-hackrf")
    capture_profile = (profile(8e6) if wrong_profile else
                       HackrfRtbwPaneProfile(request, 10e6, _COST))
    schedule = compile_pane_schedule((group,), requests, {"capture": capture_profile})
    owner = HackrfRtbwPaneOwner(g.application, physical_stream_resource_id=resource,
                                source_id="source-hackrf", receiver_endpoint_id=endpoint)
    leases = ReceiverLeaseManager()
    return PaneResourceSession(schedule, (group,), {resource: owner}, leases), leases, owner


class HackrfPaneOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.g = graph()
        self._active_session = None
        self.g.application.discover()
        self.g.application.select_device("source-hackrf")

    def tearDown(self) -> None:
        if self._active_session is not None:
            self.assertEqual(self._active_session.stop_all(), ())
        self.g.observation.close_fail = False
        for control in self.g.factory.controls:
            control.stop_fail = False
        if self.g.coordinator._quarantined_control is None:
            self.g.application.shutdown()

    def session(self, *, wrong_profile: bool = False):
        result = _session(self.g, wrong_profile=wrong_profile)
        self._active_session = result[0]
        return result

    def wait(self, predicate) -> None:
        deadline = time.monotonic() + 2.0
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded fake HackRF publication timeout")
            time.sleep(0.001)

    def test_same_common_owner_retunes_and_routes_two_ranges(self) -> None:
        session, leases, _owner = self.session()
        self.assertEqual(self.g.factory.controls, [])
        session.apply()
        first = session.start_resource("hackrf:physical")
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(len(self.g.factory.controls), 1)
        first_request = self.g.factory.controls[0].request
        self.assertIn(first_request.center_frequency_hz, (104e6, 144e6))
        self.assertEqual((first_request.lna_gain_db, first_request.vga_gain_db), (16, 20))
        self.assertEqual((first_request.rf_amplifier_enabled, first_request.bias_tee_enabled), (False, False))
        self.wait(lambda: self.g.hackrf.current_snapshot().spectrum is not None)
        delivered = session.poll_resource("hackrf:physical")
        self.assertEqual(tuple(item.pane_id for item in delivered),
                         ("low" if first_request.center_frequency_hz == 104e6 else "high",))
        self.assertEqual(delivered[0].bundle.identity.source_id, "source-hackrf")
        second = session.advance_resource("hackrf:physical")
        self.assertNotEqual(first.host_activation_serial, second.host_activation_serial)
        self.assertEqual(len(self.g.factory.controls), 2)
        second_request = self.g.factory.controls[1].request
        self.assertEqual({first_request.center_frequency_hz, second_request.center_frequency_hz},
                         {104e6, 144e6})
        self.assertEqual(self.g.factory.controls[0].stops, [5000])
        self.wait(lambda: self.g.hackrf.current_snapshot().spectrum is not None)
        self.assertEqual(tuple(item.pane_id for item in session.poll_resource("hackrf:physical")),
                         ("high" if second_request.center_frequency_hz == 144e6 else "low",))
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.g.factory.controls[1].stops, [5000])
        self.assertEqual(leases.active_resource_count, 0)

    def test_generic_single_gain_profile_is_refused_before_sdk(self) -> None:
        with self.assertRaisesRegex(PaneResourceError, "refuses a scheduled capture job"):
            self.session(wrong_profile=True)
        self.assertEqual(self.g.factory.controls, [])
        self.assertEqual(self.g.observation.probes, 0)

    def test_ordinary_live_controls_cannot_bypass_pane_claim(self) -> None:
        session, leases, _owner = self.session()
        session.apply()
        session.start_resource("hackrf:physical")
        for action in (self.g.application.discover, self.g.application.start,
                       self.g.application.stop):
            with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
                action()
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(self.g.factory.controls[0].stops, [])
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.g.factory.controls[0].stops, [5000])

    def test_unchanged_latest_snapshot_is_not_republished(self) -> None:
        session, _leases, owner = self.session()
        session.apply()
        session.start_resource("hackrf:physical")
        self.wait(lambda: self.g.hackrf.current_snapshot().spectrum is not None)
        latest = self.g.application.current_snapshot()
        with patch.object(self.g.application, "poll_published_snapshots", return_value=[latest]):
            self.assertEqual(len(owner.poll_bundles()), 1)
            self.assertEqual(owner.poll_bundles(), ())
        self.assertEqual(session.stop_all(), ())

    def test_failed_start_retains_claim_until_explicit_stop(self) -> None:
        session, leases, _owner = self.session()
        session.apply()
        self.g.native.HACKRF_UI_BRIDGE_CONTRACT_VERSION = 0
        with self.assertRaisesRegex(PaneResourceError, "explicit Stop"):
            session.start_resource("hackrf:physical")
        self.assertEqual(self.g.factory.controls, [])
        self.assertEqual(leases.active_resource_count, 1)
        with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
            self.g.application.discover()
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)

    def test_profile_preserves_full_hackrf_settings_and_bounds(self) -> None:
        request = HackrfLiveRequest(104e6, 20e6, 15_000_000, 24, 28,
                                    fft_size=16384, hop_size=8192, window="kaiser",
                                    detector="average_power", averaging_frames=8,
                                    source_id="source-hackrf")
        first = HackrfRtbwPaneProfile(request, 10e6, _COST)
        second = HackrfRtbwPaneProfile(
            HackrfLiveRequest(144e6, 20e6, 15_000_000, 24, 28,
                              fft_size=16384, hop_size=8192, window="kaiser",
                              detector="average_power", averaging_frames=8,
                              source_id="source-hackrf", configuration_generation=2),
            10e6, _COST)
        self.assertEqual(first.compatibility_key, second.compatibility_key)
        self.assertEqual((first.sample_rate_hz, first.fft_size, first.hop_size), (20e6, 16384, 8192))
        with self.assertRaisesRegex(ValueError, "usable span"):
            HackrfRtbwPaneProfile(request, 16e6, _COST)


if __name__ == "__main__":
    unittest.main()
