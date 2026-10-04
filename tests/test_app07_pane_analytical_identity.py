"""Exact outer namespace/actual cached-owner seams, no physical RF or paint proof."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sdr_monitor.domain.analytical_journal import JournalState, OwnerJournalScope, OwnerJournalSnapshot
from sdr_monitor.domain.analytical_ready import DetectorReadyReceipt, ReadyClockMapping
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.pane_analytical_identity import PaneAnalyticalIdentity
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.native_owner_journal import NativeOwnerJournal, EVENT_CAPACITY
from sdr_monitor.services.owner_journal_scope import cached_owner_journals, capture_owner_scopes
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_owner_journal import Journal, protocol
from tests.test_app07_native_ready_bridge import bridge, frame, receipt, convert
from tests.test_app07_pane_resource_session import FakeOwner, live_frame
from tests.test_app07_shared_capture_schedule import group, pane, profile


def owner_scope(**changes):
    values = dict(clock_scope_id="clock-a", host_process_id=123, owner_run_id="owner-a",
        source_id="device:source", receiver_id="RX1", session_id="fake-live-session",
        configuration_generation=5, acquisition_epoch=7)
    values.update(changes)
    return OwnerJournalScope(**values)


def ready(scope, *, offer=1, producer=7):
    return DetectorReadyReceipt(scope.clock_scope_id, scope.host_process_id, producer, offer,
        scope.configuration_generation, 10001 + offer, scope.source_id, None,
        scope.acquisition_epoch, scope.session_id, ReadyClockMapping.OUTSIDE_SAMPLES,
        owner_run_id=scope.owner_run_id)


class ScopedOwner(FakeOwner):
    def __init__(self):
        super().__init__("device")
        self.admission_mode = "rtbw"
        self.admission_generation = 5
        self.scope = None
        self.run = 0

    def start_capture(self, job):
        admitted = super().start_capture(job)
        self.run += 1
        self.scope = owner_scope(acquisition_epoch=admitted.acquisition_epoch,
                                 owner_run_id=f"owner-{self.run}")
        return replace(admitted, owner_journal_scopes=(("rx", self.scope),))


class PaneAnalyticalIdentityTests(unittest.TestCase):
    def make(self, shared=False):
        owner = ScopedOwner()
        groups = (group("device", "rx"),)
        panes = (pane("first", "rx", 100e6, 108e6,
            ReceiverBindingMode.SHARED_CAPTURE if shared else ReceiverBindingMode.DEDICATED_PARALLEL),)
        if shared:
            panes += (pane("second", "rx", 120e6, 128e6),)
        schedule = compile_pane_schedule(groups, panes,
            {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        def factory():
            fresh = ScopedOwner()
            fresh.admission_epoch = owner.admission_epoch
            fresh.run = owner.run
            return fresh
        session = PaneResourceSession(schedule, groups, {"device": owner},
            ReceiverLeaseManager(max_active_resources=4),
            source_identity_keys={"device:source": stable_identity_key("device:source")},
            owner_factories={"device": factory})
        session.apply()
        self.addCleanup(session.stop_all)
        activation = session.start_resource("device")
        return session, owner, activation

    def bundle(self, owner, ref=None):
        scope = owner.scope
        original = live_frame(scope.source_id, scope.session_id, scope.acquisition_epoch,
                              center_hz=114e6, generation=scope.configuration_generation)
        spectrum = replace(original.spectrum, detector_ready=ref or ready(scope))
        return AnalyzerFrameBundle(spectrum, original.session_id, original.receiver_id,
                                   original.acquisition_epoch, original.rtbw)

    def test_shared_panes_keep_one_offer_and_distinct_obligation_namespace(self):
        session, owner, activation = self.make(shared=True)
        bundle = self.bundle(owner)
        delivered = session.accept_frame(activation, "rx", bundle)
        self.assertEqual(len(delivered), 2)
        identities = tuple(value.analytical_identity for value in delivered)
        self.assertEqual({value.pane_id for value in identities}, {"first", "second"})
        self.assertEqual({value.ready.producer_instance_id for value in identities}, {7})
        self.assertTrue(all(value.ready is bundle.spectrum.detector_ready for value in identities))
        self.assertTrue(all(value.owner_scope is owner.scope for value in identities))
        self.assertTrue(all(value.host_run_serial == 1 and value.host_activation_serial == 1 for value in identities))
        with self.assertRaises(FrozenInstanceError):
            identities[0].capture_id = "foreign"

    def test_foreign_owner_process_clock_session_and_stale_producer_refuse(self):
        session, owner, activation = self.make()
        good = ready(owner.scope, offer=5)
        self.assertEqual(len(session.accept_frame(activation, "rx", self.bundle(owner, good))), 1)
        changes = (dict(owner_run_id="old-run"), dict(host_process_id=124),
            dict(adapter_clock_scope_id="old-clock"), dict(session_id="foreign-session"),
            dict(producer_instance_id=8), dict(offer_sequence=4), dict(ready_native_ns=999))
        for change in changes:
            with self.subTest(change=change):
                wrong = replace(good, **change)
                self.assertEqual(session.accept_frame(activation, "rx", self.bundle(owner, wrong)), ())
        self.assertEqual(session.accept_frame(activation, "rx", self.bundle(owner, good))[0].analytical_identity.ready, good)

    def test_restart_new_namespace_never_relabels_old_offer(self):
        session, owner, activation = self.make()
        old = self.bundle(owner)
        self.assertTrue(session.accept_frame(activation, "rx", old))
        session.stop_resource("device")
        session.rearm_resource("device")
        new_activation = session.start_resource("device")
        owner = session._runtimes["device"].owner
        new = self.bundle(owner)
        self.assertEqual(session.accept_frame(new_activation, "rx", old), ())
        stale = replace(new.spectrum.detector_ready, owner_run_id=old.spectrum.detector_ready.owner_run_id)
        self.assertEqual(session.accept_frame(new_activation, "rx", self.bundle(owner, stale)), ())
        item = session.accept_frame(new_activation, "rx", new)[0]
        self.assertEqual((item.host_run_serial, item.host_activation_serial), (2, 2))
        self.assertEqual(item.analytical_identity.owner_scope.owner_run_id, "owner-2")

    def test_unknown_telemetry_is_not_fabricated_or_an_acquisition_failure(self):
        session, owner, activation = self.make()
        bundle = self.bundle(owner, replace(ready(owner.scope), owner_run_id=None))
        item = session.accept_frame(activation, "rx", bundle)[0]
        self.assertIsNone(item.analytical_identity)
        self.assertTrue(owner.running)
        self.assertEqual(len(session.accept_frame(activation, "rx", self.bundle(owner))), 1)

    def test_outer_identity_and_delivery_refuse_rebinding(self):
        session, owner, activation = self.make()
        item = session.accept_frame(activation, "rx", self.bundle(owner))[0]
        for changes in (dict(host_run_serial=0), dict(host_activation_serial=True),
                        dict(pane_id="bad\x00id"), dict(capture_id=" padded ")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(item.analytical_identity, **changes)
        for changes in (dict(capture_id="other"), dict(receiver_endpoint_id="other"),
                        dict(host_run_serial=2), dict(host_activation_serial=2)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(item, **changes)
        with self.assertRaises(ValueError):
            PaneAnalyticalIdentity(owner.scope, replace(ready(owner.scope), owner_run_id=None),
                "device", "capture", "rx", "first", 1, 1)

    def test_cached_graph_seam_is_inert_and_exact_not_ui_name_parsing(self):
        scope = owner_scope()
        cached = OwnerJournalSnapshot(scope=scope, state=JournalState.ACTIVE)
        port = SimpleNamespace(analytical_journal_snapshot=Mock(return_value=cached),
                               drain=Mock(side_effect=AssertionError("no competing drain")))
        self.assertEqual(cached_owner_journals(port), (cached,))
        self.assertEqual(capture_owner_scopes(port, (("typed-rx", scope.source_id),)), (("typed-rx", scope),))
        port.drain.assert_not_called()
        with self.assertRaises(ValueError):
            capture_owner_scopes(port, (("typed-rx", "foreign"),))
        self.assertEqual(cached_owner_journals(object()), ())
        incomplete = replace(cached, state=JournalState.INCOMPLETE)
        port.analytical_journal_snapshot.return_value = incomplete
        self.assertEqual(capture_owner_scopes(port, (("typed-rx", scope.source_id),)), ())

    def test_admission_requires_common_pair_lifetime_and_exact_native_chain(self):
        session, owner, _ = self.make()
        runtime = session._runtimes["device"]
        admission = runtime.admission
        bad_chain = replace(admission,
            owner_journal_scopes=(("rx", replace(owner.scope, receiver_id="RX2")),))
        with self.assertRaises(PaneResourceError):
            session._validate_admission(runtime, runtime.current_job, bad_chain)
        first = replace(owner.scope, source_id="native-a", receiver_id="RX1")
        second = replace(owner.scope, source_id="native-b", receiver_id="RX2")
        pair = replace(admission, receiver_endpoint_ids=("rx1", "rx2"),
            endpoint_source_ids=(("rx1", "native-a"), ("rx2", "native-b")),
            owner_journal_scopes=(("rx1", first), ("rx2", second)))
        for changed in (replace(second, owner_run_id="other"),
                        replace(second, host_process_id=124),
                        replace(second, clock_scope_id="other")):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(pair, owner_journal_scopes=(("rx1", first), ("rx2", changed)))
        for malformed in ("unknown", "nul\x00run", "x" * 4097):
            with self.subTest(run=malformed), self.assertRaises(ValueError):
                replace(ready(owner.scope), owner_run_id=malformed)

    def test_native_bridge_requires_same_actual_producer_and_offer_counter(self):
        value, native, _ = bridge()
        native = protocol(native)
        scope = OwnerJournalScope(value.clock_scope_id, value.host_process_id, "actual-run",
                                  "chain", "RX2", "session", 5, 9)
        consumer = NativeOwnerJournal(native, EVENT_CAPACITY)
        consumer.begin(scope)
        journal = Journal()
        journal.offer(2)
        consumer.drain(journal.read)
        def raw_ref(**changes):
            ref = receipt(**changes)
            ref.clock = native.AnalyticalReadyClock.NativeSteady
            ref.clock_state = native.AnalyticalReadyClockState.Monotonic
            return ref
        original = convert(value, frame(raw_ref()), owner_journal=consumer.current())
        self.assertEqual(original.owner_run_id, "actual-run")
        for ref in (raw_ref(producer=8), raw_ref(sequence=3)):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                convert(value, frame(ref), owner_journal=consumer.current())
        bad = replace(consumer.current(), scope=replace(scope, owner_run_id="same-run",
                      source_id="foreign"))
        with self.assertRaises(ValueError):
            convert(value, frame(raw_ref()), owner_journal=bad)
        incomplete = replace(consumer.current(), state=JournalState.INCOMPLETE)
        self.assertIsNone(convert(value, frame(raw_ref()), owner_journal=incomplete).owner_run_id)
        self.assertEqual(journal.calls, 1)  # Cached conversions never drain.


if __name__ == "__main__":
    unittest.main()
