"""SAME owned Start receiver readback, never an incoming-frame wildcard."""

from dataclasses import replace
from enum import Enum
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, compile_pane_schedule
from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneCaptureAdmission, PaneResourceError, PaneResourceSession

from tests import test_app07_ad936x_rtbw_pane_owner as ad_fixture
from tests import test_app07_pane_resource_session as session_fixture
from tests.test_app07_shared_capture_schedule import group, pane, profile
from tests.test_s15_live_rx_bridge import _make_frame, _wait_for


class _Selection(Enum):
    RX1 = 1
    RX2 = 2
    BOTH = 3


class _SelectedNative(ad_fixture._ReadbackNative):
    PlutoReceiverSelection = _Selection
    selection = _Selection.RX1

    def PlutoFixedBandEngine(self, uri, timeout_ms):
        engine = super().PlutoFixedBandEngine(uri, timeout_ms)
        configure_original = engine.configure

        def configure(config):
            actual = configure_original(config)
            actual.receiver_selection = self.selection
            return actual

        engine.configure = configure
        return engine


class _ReceiptOwner(session_fixture.FakeOwner):
    receiver_readback = "RX1"

    def start_capture(self, job):
        admission = super().start_capture(job)
        return replace(admission, endpoint_receiver_ids=((job.receiver_endpoint_ids[0], self.receiver_readback),))


class StartReceiverAdmissionTests(unittest.TestCase):
    def graph(self, *, selected=True):
        fixture = ad_fixture.Ad936xRtbwPaneOwnerTests()
        with patch.object(ad_fixture, "_ReadbackNative", _SelectedNative if selected else ad_fixture._ReadbackNative):
            native, service, live, source = fixture.graph()
        self.addCleanup(service.stop_and_wait, 5.0)
        session, leases = fixture.session(live, source)
        self.addCleanup(lambda: self.assertEqual(session.stop_all(), ()))
        return native, service, live, source, session, leases

    def test_original_native_to_domain_rx1_reaches_pane_without_inert_stage_guess(self):
        native, service, live, source, session, leases = self.graph()
        self.assertEqual(native.engines, [])
        self.assertIsNone(session._expected_receivers["rx-a"])
        session.apply()
        self.assertEqual(native.engines, [])
        session.start_resource("physical-a")
        self.assertEqual(service.latest_snapshot().receiver_id, "RX1")
        center = service.latest_snapshot().applied.applied.center_hz
        frame = _make_frame(1, center_hz=center, sample_rate_hz=20e6,
                            source_id=source, config_generation=1)
        frame.frequencies_hz = center + (np.arange(4096) - 2048) * (20e6 / 4096)
        native.engines[0].frames.append(frame)
        self.assertTrue(_wait_for(lambda: service.latest_snapshot().spectrum is not None))
        self.assertEqual(live.current_analyzer_bundle().identity.receiver_id, "RX1")
        deliveries = session.poll_resource("physical-a")
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].bundle.identity.receiver_id, "RX1")
        self.assertEqual(session.rejected_publications("physical-a"), 0)
        self.assertEqual(len(native.engines), 1)
        self.assertEqual(leases.active_resource_count, 1)

    def make_session(self, *, staged_receiver=None, owner_factory=None):
        fixture = session_fixture.PaneResourceSessionTests()
        fixture.setUp()
        owner = _ReceiptOwner("device-a")
        owner.admission_mode = "rtbw"
        owner.admission_generation = 5
        if staged_receiver is not None:
            owner.receiver_ids = {"rx-a": staged_receiver}
        groups = (group("device-a", "rx-a"),)
        schedule = compile_pane_schedule(groups,
            (pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),),
            {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        session = PaneResourceSession(schedule, groups, {"device-a": owner}, fixture.leases,
            source_identity_keys={"device-a:source": stable_identity_key("device-a:source")},
            now_s=lambda: fixture.clock[0],
            owner_factories={} if owner_factory is None else {"device-a": owner_factory})
        self.addCleanup(lambda: self.assertEqual(session.stop_all(), ()))
        session.apply()
        return session, owner, fixture.leases

    def test_start_binding_rejects_unknown_foreign_rx_and_other_capture_identities(self):
        session, owner, _ = self.make_session()
        activation = session.start_resource("device-a")
        valid = dict(source_id="device-a:source", session_id=owner.admission_session_id,
                     epoch=7, receiver_id="RX1")
        for change in (dict(receiver_id=None), dict(receiver_id="RX2"),
                       dict(source_id="foreign"), dict(session_id="old"),
                       dict(epoch=6), dict(generation=4), dict(sample_rate_hz=20e6),
                       dict(fft_size=2048), dict(hop_size=1024)):
            with self.subTest(change=change):
                frame = session_fixture.live_frame(**(valid | change))
                self.assertEqual(session.accept_frame(activation, "rx-a", frame), ())
        self.assertEqual(session.rejected_publications("device-a"), 9)
        self.assertEqual(len(session.accept_frame(activation, "rx-a", session_fixture.live_frame(**valid))), 1)

    def test_rx2_start_readback_refuses_rx1_endpoint_and_retains_explicit_stop(self):
        session, owner, leases = self.make_session()
        owner.receiver_readback = "RX2"
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("device-a")
        self.assertTrue(owner.running)
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertFalse(owner.running)
        self.assertEqual(leases.active_resource_count, 0)

    def test_known_staged_receiver_cannot_be_rebound_by_start_receipt(self):
        session, owner, _ = self.make_session(staged_receiver="another-producer")
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("device-a")
        self.assertTrue(owner.running)

    def test_restart_requires_new_start_binding_not_old_frame_authority(self):
        following = _ReceiptOwner("device-a")
        following.admission_mode = "rtbw"
        following.admission_generation = 5
        following.admission_epoch = 8
        session, owner, _ = self.make_session(owner_factory=lambda: following)
        first = session.start_resource("device-a")
        old = session_fixture.live_frame("device-a:source", owner.admission_session_id, 7, receiver_id="RX1")
        self.assertEqual(len(session.accept_frame(first, "rx-a", old)), 1)
        session.stop_resource("device-a")
        session.rearm_resource("device-a")
        second = session.start_resource("device-a")
        self.assertEqual(session.accept_frame(first, "rx-a", old), ())
        self.assertEqual(session.accept_frame(second, "rx-a", old), ())
        current = session_fixture.live_frame("device-a:source", owner.admission_session_id, 8, receiver_id="RX1")
        self.assertEqual(len(session.accept_frame(second, "rx-a", current)), 1)

    def test_ad_actual_rx2_readback_is_not_authorized_by_rx1_schedule(self):
        with patch.object(_SelectedNative, "selection", _Selection.RX2):
            native, _service, _live, _source, session, leases = self.graph()
            session.apply()
            with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
                session.start_resource("physical-a")
        self.assertEqual(len(native.engines), 1)
        self.assertEqual(leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)

    def test_legacy_no_readback_is_not_fabricated_as_rx1(self):
        _native, service, _live, source, session, _leases = self.graph(selected=False)
        session.apply()
        activation = session.start_resource("physical-a")
        current = service.latest_snapshot()
        self.assertIsNone(current.receiver_id)
        self.assertEqual(session._runtimes["physical-a"].admission.endpoint_receiver_ids, ())
        args = dict(source_id=source, session_id=current.session_id,
                    epoch=current.acquisition_epoch, generation=current.active_config_generation,
                    center_hz=current.applied.applied.center_hz, sample_rate_hz=20e6)
        self.assertEqual(session.accept_frame(activation, "rx-a", session_fixture.live_frame(**args, receiver_id="RX1")), ())
        self.assertEqual(len(session.accept_frame(activation, "rx-a", session_fixture.live_frame(**args))), 1)

    def test_readback_binding_requires_immutable_exact_order_and_canonical_receivers(self):
        admission = PaneCaptureAdmission("capture", "source", "rtbw", "dBFS/bin", ("rx-a",), 1,
            session_id="session", sample_rate_hz=20e6, fft_size=4096, hop_size=2048)
        for malformed in ([('rx-a', 'RX1')], (("foreign", "RX1"),), (("rx-a", None),),
                          (("rx-a", "rx1"),), (("rx-a", "BOTH"),),
                          (("rx-a", "RX1"), ("rx-a", "RX2"))):
            with self.subTest(binding=malformed), self.assertRaises(ValueError):
                replace(admission, endpoint_receiver_ids=malformed)
        valid = replace(admission, endpoint_receiver_ids=(("rx-a", "RX1"),))
        self.assertEqual(valid.producer_receiver_id("rx-a", None), "RX1")
        self.assertIsNone(admission.producer_receiver_id("rx-a", None))
        self.assertEqual(admission.producer_receiver_id("rx-a", "legacy"), "legacy")
        with self.assertRaises(ValueError):
            valid.producer_receiver_id("foreign", None)
        with self.assertRaises(ValueError):
            replace(valid, mode="sweep", session_id=None)

    def test_pair_binding_order_uniqueness_and_analytical_scope_must_match(self):
        admission = PaneCaptureAdmission("capture", "source", "rtbw", "dBFS/bin", ("rx-a", "rx-b"), 1,
            session_id="session", config_generation=2, sample_rate_hz=20e6, fft_size=4096, hop_size=2048,
            endpoint_source_ids=(("rx-a", "native-a"), ("rx-b", "native-b")),
            endpoint_receiver_ids=(("rx-a", "RX1"), ("rx-b", "RX2")))
        for bindings in ((("rx-b", "RX2"), ("rx-a", "RX1")),
                         (("rx-a", "RX1"), ("rx-b", "RX1"))):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                replace(admission, endpoint_receiver_ids=bindings)
        first = OwnerJournalScope("clock", 1, "owner", "native-a", "RX1", "session", 2, 1)
        second = replace(first, source_id="native-b", receiver_id="RX2")
        scoped = replace(admission, owner_journal_scopes=(("rx-a", first), ("rx-b", second)))
        for receiver in (None, "RX1"):
            with self.subTest(receiver=receiver), self.assertRaises(ValueError):
                replace(scoped, owner_journal_scopes=(("rx-a", first),
                    ("rx-b", replace(second, receiver_id=receiver))))


if __name__ == "__main__":
    unittest.main()
