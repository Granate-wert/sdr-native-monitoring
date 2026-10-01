"""Real common resource lifecycle for RF-plan changes, without SDK or Qt."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.pane_scheduler import (
    PaneControlGapReason, compile_pane_schedule,
)
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_pane_resource_session import FakeOwner, frame
from tests.test_app07_shared_capture_schedule import group, pane, profile


class ResourceRangeChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [10.0]
        self.groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        self.requests = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("peer", "rx-b", 200e6, 208e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        self.owners = {key: FakeOwner(key) for key in ("device-a", "device-b")}
        self.fresh: dict[str, list[FakeOwner]] = {key: [] for key in self.owners}
        self.leases = ReceiverLeaseManager(max_active_resources=4)
        self.session = PaneResourceSession(self.compile(self.requests), self.groups, self.owners, self.leases,
            source_identity_keys={endpoint.source_id: stable_identity_key(endpoint.source_id)
                                  for group_ in self.groups for endpoint in group_.endpoints},
            owner_factories={key: lambda key=key: self.new_owner(key) for key in self.owners},
            now_s=lambda: self.clock[0])
        self.session.apply()

    def compile(self, requests):
        return compile_pane_schedule(self.groups, requests, {"capture": profile(36e6)})

    def shifted(self):
        return self.compile((replace(self.requests[0], start_hz=120e6, stop_hz=128e6), self.requests[1]))

    def new_owner(self, key: str) -> FakeOwner:
        previous = self.fresh[key][-1] if self.fresh[key] else self.owners[key]
        owner = FakeOwner(key)
        owner.admission_epoch = previous.admission_epoch
        owner.clock = self.clock
        owner.start_cost_s = 0.05
        self.fresh[key].append(owner)
        return owner

    def tearDown(self) -> None:
        for owner in (*self.owners.values(), *(owner for items in self.fresh.values() for owner in items)):
            owner.fail_stop = False
            owner.recording = False
        self.assertEqual(self.session.stop_all(), ())
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_preview_inert_apply_requires_stop_then_fresh_start_and_peer_untouched(self) -> None:
        old = self.session.start_resource("device-a")
        peer = self.session.start_resource("device-b")
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.assertEqual(preview.affected_pane_ids, ("first",))
        self.assertTrue(preview.restart_required)
        self.assertEqual(self.owners["device-a"].events, [("start", old.capture_id)])
        with self.assertRaisesRegex(PaneResourceError, "Stop has not fully released"):
            self.session.replace_stopped_resource_plan(preview)
        self.session.stop_resource("device-a")
        self.session.replace_stopped_resource_plan(preview)
        self.assertIs(self.session.schedule, preview.proposed_schedule)
        self.assertEqual(self.fresh["device-a"], [])  # Apply did not create/start an owner.
        self.session.rearm_resource("device-a")
        current = self.session.start_resource("device-a")
        self.assertGreater(current.host_activation_serial, old.host_activation_serial)
        self.assertIs(current.planned_control_gap.reason, PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE)
        self.assertEqual(current.planned_control_gap.previous_capture_id, old.capture_id)
        self.assertEqual(self.session.accept_frame(old, "rx-a", frame("device-a:source", 7, 100e6, 108e6)), ())
        self.assertEqual(len(self.session.accept_frame(current, "rx-a", frame("device-a:source", 8, 120e6, 128e6))), 1)
        self.assertEqual(len(self.session.accept_frame(peer, "rx-b", frame("device-b:source", 7, 200e6, 208e6))), 1)
        self.assertEqual(self.owners["device-b"].events, [("start", peer.capture_id)])

    def test_preview_refuses_incomplete_or_foreign_impact_and_untyped_run_flags(self) -> None:
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        for fields in ({"affected_pane_ids": ()}, {"affected_pane_ids": ("peer",)},
                       {"expected_run_serial": True}, {"restart_required": 0}):
            with self.subTest(fields=fields), self.assertRaises(PaneResourceError):
                replace(preview, **fields)
        mutable = ["first"]
        frozen = replace(preview, affected_pane_ids=mutable)
        mutable.clear()
        self.assertEqual(frozen.affected_pane_ids, ("first",))
        self.assertEqual(self.owners["device-a"].events, [])

    def test_observed_control_gap_includes_confirmed_stop_wait_and_start_not_rf_duty(self) -> None:
        owner = self.owners["device-a"]
        owner.clock, owner.stop_cost_s = self.clock, 0.2
        self.session.start_resource("device-a")
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.session.stop_resource("device-a")
        self.clock[0] += 0.5  # Explicit user/GUI wait is not secretly continuous RF.
        self.session.replace_stopped_resource_plan(preview)
        self.session.rearm_resource("device-a")
        current = self.session.start_resource("device-a")
        self.assertAlmostEqual(current.host_control_elapsed_s, 0.75)
        self.assertAlmostEqual(current.planned_control_gap.planned_duration_s, 0.02)

    def test_stop_failure_retains_old_plan_lease_and_prevents_new_start(self) -> None:
        self.session.start_resource("device-a")
        old_schedule = self.session.schedule
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.owners["device-a"].fail_stop = True
        with self.assertRaises(PaneResourceError):
            self.session.stop_resource("device-a")
        with self.assertRaises(PaneResourceError):
            self.session.replace_stopped_resource_plan(preview)
        self.assertIs(self.session.schedule, old_schedule)
        self.assertEqual(self.leases.active_resource_count, 2)
        self.assertEqual(self.fresh["device-a"], [])

    def test_recording_refused_before_stop_and_rechecked_after_stop(self) -> None:
        self.session.start_resource("device-a")
        self.owners["device-a"].recording = True
        with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
            self.session.preview_resource_plan("device-a", self.shifted())
        self.assertTrue(self.owners["device-a"].running)
        self.owners["device-a"].recording = False
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.session.stop_resource("device-a")
        self.owners["device-a"].recording = True
        with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
            self.session.replace_stopped_resource_plan(preview)
        self.assertIs(self.session.schedule, preview.expected_schedule)

    def test_new_run_invalidates_preview_even_if_schedule_unchanged(self) -> None:
        self.session.start_resource("device-a")
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.session.stop_resource("device-a")
        self.session.rearm_resource("device-a")
        self.session.start_resource("device-a")
        self.session.stop_resource("device-a")
        with self.assertRaisesRegex(PaneResourceError, "stale"):
            self.session.replace_stopped_resource_plan(preview)

    def test_preview_cannot_change_peer_mode_unit_route_or_deadline(self) -> None:
        candidates = [self.compile((replace(self.requests[0], start_hz=120e6, stop_hz=128e6),
                                    replace(self.requests[1], start_hz=220e6, stop_hz=228e6)))]
        new = self.shifted()
        candidates.append(replace(new, pane_revisits=(replace(new.pane_revisits[0], requested_maximum_revisit_s=0.001),
                                                       new.pane_revisits[1])))
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(PaneResourceError):
                self.session.preview_resource_plan("device-a", candidate)
        self.assertEqual([owner.events for owner in self.owners.values()], [[], []])

    def test_applied_preview_cannot_be_reused(self) -> None:
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.session.stop_resource("device-a")
        self.session.replace_stopped_resource_plan(preview)
        with self.assertRaisesRegex(PaneResourceError, "stale"):
            self.session.replace_stopped_resource_plan(preview)

    def test_guarded_rf_stop_rechecks_recording_before_touching_running_owner(self) -> None:
        activation = self.session.start_resource("device-a")
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.owners["device-a"].recording = True
        with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
            self.session.stop_for_resource_plan(preview)
        self.assertEqual(self.owners["device-a"].events, [("start", activation.capture_id)])
        self.assertTrue(self.owners["device-a"].running)

    def test_guarded_rf_stop_never_stops_a_newer_explicit_run(self) -> None:
        self.session.start_resource("device-a")
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.session.stop_resource("device-a")
        self.session.rearm_resource("device-a")
        activation = self.session.start_resource("device-a")
        with self.assertRaisesRegex(PaneResourceError, "stale"):
            self.session.stop_for_resource_plan(preview)
        self.assertEqual(self.fresh["device-a"][-1].events, [("start", activation.capture_id)])

    def test_unknown_stop_clock_stays_unknown_instead_of_reporting_start_only(self) -> None:
        self.session.start_resource("device-a")
        preview = self.session.preview_resource_plan("device-a", self.shifted())
        self.clock[0] = float("nan")
        self.session.stop_for_resource_plan(preview)
        self.clock[0] = 20
        self.session.replace_stopped_resource_plan(preview)
        self.session.rearm_resource("device-a")
        self.assertIsNone(self.session.start_resource("device-a").host_control_elapsed_s)

    def test_multiple_applies_before_start_keep_gap_from_last_real_admission(self) -> None:
        old = self.session.start_resource("device-a")
        first = self.session.preview_resource_plan("device-a", self.shifted())
        self.session.stop_for_resource_plan(first)
        self.session.replace_stopped_resource_plan(first)
        next_schedule = self.compile((replace(self.requests[0], start_hz=130e6, stop_hz=138e6), self.requests[1]))
        second = self.session.preview_resource_plan("device-a", next_schedule)
        self.session.replace_stopped_resource_plan(second)
        self.session.rearm_resource("device-a")
        current = self.session.start_resource("device-a")
        self.assertEqual(current.planned_control_gap.previous_capture_id, old.capture_id)
        self.assertEqual(current.planned_control_gap.next_capture_id, current.capture_id)
        self.assertIs(current.planned_control_gap.reason, PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE)

    def test_shared_to_sliced_preview_impacts_both_panes_and_actual_last_capture_defines_gap(self) -> None:
        self.session.stop_all()
        self.requests = (pane("first", "rx-a", 100e6, 104e6),
                         pane("neighbor", "rx-a", 106e6, 110e6), self.requests[1])
        self.session = PaneResourceSession(self.compile(self.requests), self.groups, self.owners, self.leases,
            source_identity_keys={endpoint.source_id: stable_identity_key(endpoint.source_id)
                                  for group_ in self.groups for endpoint in group_.endpoints},
            owner_factories={key: lambda key=key: self.new_owner(key) for key in self.owners},
            now_s=lambda: self.clock[0])
        self.session.apply()
        old = self.session.start_resource("device-a")
        incoming = self.compile((replace(self.requests[0], start_hz=140e6, stop_hz=144e6,
                                         requested_binding_mode=ReceiverBindingMode.TIME_SLICED),
                                 replace(self.requests[1], requested_binding_mode=ReceiverBindingMode.TIME_SLICED),
                                 self.requests[2]))
        preview = self.session.preview_resource_plan("device-a", incoming)
        self.assertEqual(preview.affected_pane_ids, ("first", "neighbor"))
        self.session.stop_for_resource_plan(preview)
        self.session.replace_stopped_resource_plan(preview)
        self.session.rearm_resource("device-a")
        new = self.session.start_resource("device-a")
        self.assertEqual(new.planned_control_gap.previous_capture_id, old.capture_id)
        self.assertEqual(len(self.session.schedule.resources[0].jobs), 2)

    def test_timeslice_advance_does_not_stale_preview_and_gap_uses_actual_stop_slot(self) -> None:
        self.session.stop_all()
        self.requests = (pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                         pane("neighbor", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED), self.requests[1])
        self.session = PaneResourceSession(self.compile(self.requests), self.groups, self.owners, self.leases,
            source_identity_keys={endpoint.source_id: stable_identity_key(endpoint.source_id)
                                  for group_ in self.groups for endpoint in group_.endpoints},
            owner_factories={key: lambda key=key: self.new_owner(key) for key in self.owners},
            now_s=lambda: self.clock[0])
        self.session.apply()
        initial = self.session.start_resource("device-a")
        incoming = self.compile((replace(self.requests[0], start_hz=160e6, stop_hz=168e6),
                                 self.requests[1], self.requests[2]))
        preview = self.session.preview_resource_plan("device-a", incoming)
        last = self.session.advance_resource("device-a")
        self.assertNotEqual(last.capture_id, initial.capture_id)
        self.session.stop_for_resource_plan(preview)
        self.session.replace_stopped_resource_plan(preview)
        self.session.rearm_resource("device-a")
        current = self.session.start_resource("device-a")
        self.assertEqual(current.planned_control_gap.previous_capture_id, last.capture_id)


if __name__ == "__main__":
    unittest.main()
