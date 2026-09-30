"""Explicit fresh resource runs; no SDK, device discovery or Qt is used."""

import unittest

from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.pane_scheduler import compile_pane_schedule
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_pane_resource_session import FakeOwner, frame
from tests.test_app07_shared_capture_schedule import group, pane, profile


class ResourceRearmTests(unittest.TestCase):
    def setUp(self):
        self.groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        self.owners = {resource: FakeOwner(resource) for resource in ("device-a", "device-b")}
        self.created = []
        self.change = lambda owner: None
        self.leases = ReceiverLeaseManager(max_active_resources=4)

        def factory(resource):
            owner = FakeOwner(resource)
            owner.admission_epoch = 8 + len(self.created)
            self.change(owner)
            self.created.append(owner)
            return owner

        requests = tuple(pane(name, endpoint, start, start + 8e6,
                              ReceiverBindingMode.DEDICATED_PARALLEL)
                         for name, endpoint, start in (("a", "rx-a", 100e6), ("b", "rx-b", 140e6)))
        schedule = compile_pane_schedule(self.groups, requests, {"capture": profile(36e6)})
        self.session = PaneResourceSession(
            schedule, self.groups, self.owners, self.leases,
            source_identity_keys={group.endpoints[0].source_id: stable_identity_key(group.group_id)
                                  for group in self.groups},
            owner_factories={resource: lambda resource=resource: factory(resource) for resource in self.owners})
        self.session.apply()

    def tearDown(self):
        for runtime in self.session._runtimes.values():
            runtime.owner.fail_stop = False
        self.assertEqual(self.session.stop_all(), ())
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_clean_stop_rearm_new_owner_and_run_leave_peer_admission_unchanged(self):
        first = self.session.start_resource("device-a")
        peer = self.session.start_resource("device-b")
        bundle = frame("device-a:source", 7, 100e6, 108e6)
        before = self.session.accept_frame(first, "rx-a", bundle)[0]
        self.assertEqual(before.host_run_serial, 1)
        self.session.stop_resource("device-a")
        self.assertTrue(self.session.can_rearm_resource("device-a"))
        self.session.rearm_resource("device-a")
        self.assertFalse(self.session.can_rearm_resource("device-a"))
        self.assertEqual(self.session.pane_age_s("a"), None)
        owner = self.session._runtimes["device-a"].owner
        self.assertIsNot(owner, self.owners["device-a"])
        self.assertIs(self.session._runtimes["device-b"].current_activation, peer)
        self.assertEqual(self.leases.active_resource_count, 2)
        second = self.session.start_resource("device-a")
        self.assertGreater(second.host_activation_serial, first.host_activation_serial)
        self.assertEqual(self.session.accept_frame(first, "rx-a", bundle), ())
        self.assertEqual(self.session.accept_frame(second, "rx-a", bundle), ())
        after = self.session.accept_frame(second, "rx-a", frame("device-a:source", 8, 100e6, 108e6))[0]
        self.assertEqual(after.host_run_serial, 2)
        self.assertIs(self.session._runtimes["device-b"].current_activation, peer)

    def test_failed_stop_cannot_construct_or_rearm_a_second_owner(self):
        self.session.start_resource("device-a")
        self.owners["device-a"].fail_stop = True
        with self.assertRaisesRegex(PaneResourceError, "owner and lease retained"):
            self.session.stop_resource("device-a")
        self.assertFalse(self.session.can_rearm_resource("device-a"))
        with self.assertRaisesRegex(PaneResourceError, "fully released"):
            self.session.rearm_resource("device-a")
        self.assertEqual(self.created, [])
        self.assertIs(self.session._runtimes["device-a"].owner, self.owners["device-a"])

    def test_changed_receiver_identity_and_recording_refuse_before_new_lease(self):
        self.session.start_resource("device-a")
        self.session.stop_resource("device-a")
        for change in (lambda owner: owner.receiver_ids.update({"rx-a": "different"}),
                       lambda owner: setattr(owner, "recording", True)):
            with self.subTest(change=change):
                self.change = change
                with self.assertRaisesRegex(PaneResourceError, "existing plan"):
                    self.session.rearm_resource("device-a")
                self.assertEqual(self.leases.active_resource_count, 1)
                self.assertIs(self.session._runtimes["device-a"].owner, self.owners["device-a"])

    def test_reused_adapter_or_foreign_lease_is_not_stolen(self):
        self.session.start_resource("device-a")
        self.session.stop_resource("device-a")
        factory = self.session._owner_factories["device-a"]
        self.session._owner_factories["device-a"] = lambda: self.owners["device-a"]
        with self.assertRaisesRegex(PaneResourceError, "existing plan"):
            self.session.rearm_resource("device-a")
        self.session._owner_factories["device-a"] = factory
        foreign = self.leases.acquire(self.groups[0])
        try:
            with self.assertRaisesRegex(PaneResourceError, "lease is unavailable"):
                self.session.rearm_resource("device-a")
            self.assertIsNone(self.session._runtimes["device-a"].lease)
            self.assertEqual(self.leases.active_resource_count, 2)
        finally:
            foreign.release()

    def test_same_sweep_epoch_on_new_adapter_retains_new_owner_until_stop(self):
        self.session.start_resource("device-a")
        self.session.stop_resource("device-a")
        self.change = lambda owner: setattr(owner, "admission_epoch", 7)
        self.session.rearm_resource("device-a")
        with self.assertRaisesRegex(PaneResourceError, "explicit Stop"):
            self.session.start_resource("device-a")
        self.assertEqual(self.leases.active_resource_count, 2)
        self.assertTrue(self.session._runtimes["device-a"].owner.running)
        self.assertTrue(self.session._runtimes["device-a"].stop_required)
        self.assertFalse(self.session.can_rearm_resource("device-a"))

    def test_terminal_retirement_blocks_all_fresh_factories_and_apply(self):
        self.assertEqual(self.session.stop_all(), ())
        self.session.retire_after_stop()
        self.assertEqual(self.session._owner_factories, {})
        self.assertFalse(self.session.can_rearm_resource("device-a"))
        with self.assertRaisesRegex(PaneResourceError, "fully released"):
            self.session.rearm_resource("device-a")
        with self.assertRaisesRegex(PaneResourceError, "terminated"):
            self.session.apply()
        self.assertEqual(self.created, [])


if __name__ == "__main__":
    unittest.main()
