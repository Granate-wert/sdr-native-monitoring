"""APP-07 same-owner tinySA trace panes; fake serial, no physical RF claim."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace

from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, PaneScheduleError, SpectrumTracePaneProfile,
    TinySaTracePaneProfile, compile_pane_schedule,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, SpectrumTraceEndpoint,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.tinysa_trace_pane_owner import TinySaTracePaneOwner

from tests.test_app07_shared_capture_schedule import pane
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph


_COST = CaptureEpochCost(0.02, 0.02, 1.0, 0.01, 0.01)


def _session(g, *, generic_profile: bool = False):
    selected = g.sources.current()
    assert selected.selected is not None
    request = TinySaSweepRequest(selected.selected, selected.revision,
                                 87_500_000, 108_000_000, 3)
    resource, endpoint = "tinysa:physical", "tinysa:trace"
    group = AcquisitionGroup("tinysa:group", resource, (
        SpectrumTraceEndpoint(endpoint, selected.selected.device_id, resource),))
    profile = (SpectrumTracePaneProfile(3, 21e6, "ultra-default", _COST)
               if generic_profile else TinySaTracePaneProfile(
                   3, 21e6, "ultra-default", _COST, request_template=request))
    schedule = compile_pane_schedule((group,), (
        pane("low", endpoint, 87_500_000, 108_000_000, ReceiverBindingMode.TIME_SLICED),
        pane("high", endpoint, 200_000_000, 210_000_000, ReceiverBindingMode.TIME_SLICED),
    ), {"capture": profile})
    owner = TinySaTracePaneOwner(g.application, g.instrument,
                                 physical_stream_resource_id=resource,
                                 source_id=selected.selected.device_id,
                                 trace_endpoint_id=endpoint)
    leases = ReceiverLeaseManager()
    return PaneResourceSession(schedule, (group,), {resource: owner}, leases), leases, owner


class TinySaTracePaneOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.g = graph()
        selected = self.g.application.discover()[0]
        self.g.application.select_device(selected.device_id)
        self._active_session = None

    def tearDown(self) -> None:
        if self._active_session is not None:
            self.assertEqual(self._active_session.stop_all(), ())
        self.g.application.shutdown()

    def session(self, *, generic_profile: bool = False):
        result = _session(self.g, generic_profile=generic_profile)
        self._active_session = result[0]
        return result

    def wait(self, predicate) -> None:
        deadline = time.monotonic() + 4.0
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded fake tinySA trace timeout")
            time.sleep(0.001)

    def test_same_serial_owner_time_slices_two_ranges_with_true_stop_edge(self) -> None:
        session, leases, _owner = self.session()
        self.assertEqual(self.g.serials, [])
        session.apply()
        first = session.start_resource("tinysa:physical")
        self.wait(lambda: self.g.instrument.poll_latest().line is not None)
        first_line = self.g.instrument.poll_latest().line
        first_stop = first_line.instrument.stop_hz
        self.assertIn(first_stop, (108_000_000, 210_000_000))
        self.assertLess(float(first_line.frequencies_hz[-1]), first_stop)
        delivered = session.poll_resource("tinysa:physical")
        first_pane = "low" if first_stop == 108_000_000 else "high"
        self.assertEqual(tuple(item.pane_id for item in delivered), (first_pane,))
        self.assertEqual(delivered[0].bundle.unit, "dBm")
        self.assertIsNone(delivered[0].bundle.rtbw)
        self.assertEqual(leases.active_resource_count, 1)
        second = session.advance_resource("tinysa:physical")
        self.assertNotEqual(first.host_activation_serial, second.host_activation_serial)
        self.wait(lambda: self.g.instrument.poll_latest().line is not None)
        delivered = session.poll_resource("tinysa:physical")
        self.assertEqual(tuple(item.pane_id for item in delivered),
                         ("high" if first_pane == "low" else "low",))
        self.assertEqual(delivered[0].bundle.acquisition_epoch, 2)
        self.assertEqual(len(self.g.serials), 2)
        self.assertFalse(self.g.serials[0].is_open)
        self.assertEqual(session.stop_all(), ())
        self.assertFalse(self.g.serials[1].is_open)
        self.assertEqual(leases.active_resource_count, 0)

    def test_unbound_generic_trace_profile_refused_before_serial(self) -> None:
        with self.assertRaisesRegex(PaneResourceError, "refuses a scheduled capture job"):
            self.session(generic_profile=True)
        self.assertEqual(self.g.serials, [])

    def test_ordinary_analyzer_stop_cannot_steal_pane_serial_owner(self) -> None:
        session, leases, _owner = self.session()
        session.apply()
        session.start_resource("tinysa:physical")
        with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
            self.g.application.stop()
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)

    def test_cached_last_trace_is_not_republished(self) -> None:
        session, _leases, owner = self.session()
        session.apply()
        session.start_resource("tinysa:physical")
        self.wait(lambda: self.g.instrument.poll_latest().line is not None)
        self.assertEqual(len(owner.poll_bundles()), 1)
        self.assertEqual(owner.poll_bundles(), ())
        self.assertEqual(session.stop_all(), ())

    def test_profile_preserves_typed_settings_and_rejects_point_mismatch(self) -> None:
        selected = self.g.sources.current()
        assert selected.selected is not None
        request = TinySaSweepRequest(selected.selected, selected.revision,
                                     87_500_000, 108_000_000, 3)
        first = TinySaTracePaneProfile(3, 21e6, "ultra-default", _COST,
                                       request_template=request)
        second = TinySaTracePaneProfile(3, 21e6, "ultra-default", _COST,
                                        request_template=replace(request, start_hz=200_000_000,
                                                                 stop_hz=210_000_000, epoch=5))
        self.assertEqual(first.compatibility_key, second.compatibility_key)
        with self.assertRaisesRegex(PaneScheduleError, "points/span"):
            TinySaTracePaneProfile(4, 21e6, "ultra-default", _COST,
                                   request_template=request)


if __name__ == "__main__":
    unittest.main()
