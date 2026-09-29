"""APP-07 worker control and bounded handoff; no device or Qt is opened."""

from __future__ import annotations

import time
import unittest
from dataclasses import replace
from threading import Lock
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PaneResourcePump, _ResourceWorker

from tests.test_app07_pane_resource_session import FakeOwner, frame, live_frame
from tests.test_app07_shared_capture_schedule import group, pane, profile
from tests.ui_v2.test_app04_progressive_waterfall import terminal


class _SafeOwner(FakeOwner):
    def __init__(self, resource_id: str) -> None:
        super().__init__(resource_id)
        self._publications_lock = Lock()

    def publish(self, bundle) -> None:
        with self._publications_lock:
            self.publications.append((self.physical_stream_resource_id.replace("device", "rx"), bundle))

    def poll_bundles(self):
        with self._publications_lock:
            return super().poll_bundles()


class PaneResourcePumpTests(unittest.TestCase):
    @staticmethod
    def wait_until(predicate, *, timeout_s: float = 3.0) -> None:
        deadline = time.monotonic() + timeout_s
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("bounded pane worker observation timed out")
            time.sleep(0.002)

    @staticmethod
    def make_plan(count: int = 3, *, time_sliced: bool = False,
                  mode: CaptureMeasurementMode = CaptureMeasurementMode.SWEEP):
        names = ("a", "b", "c")[:count]
        groups = tuple(group("device-1" if time_sliced else f"device-{index}",
                             "rx-1" if time_sliced else f"rx-{index}")
                       for index in (range(1, 2) if time_sliced else range(1, count + 1)))
        slots = tuple(PaneLayoutSlot(index, pane(
            name, "rx-1" if time_sliced else f"rx-{index}",
            start, start + 8e6,
            ReceiverBindingMode.TIME_SLICED if time_sliced else ReceiverBindingMode.DEDICATED_PARALLEL))
            for index, (name, start) in enumerate(zip(names, (100e6, 140e6, 200e6)), 1))
        if count == 3:
            slots += (PaneLayoutSlot(4),)
        layout = compile_pane_layout(slots, groups, {"capture": profile(20e6, mode)})
        assert layout.schedule is not None
        owners = {item.physical_stream_resource_id: _SafeOwner(item.physical_stream_resource_id)
                  for item in groups}
        leases = ReceiverLeaseManager(max_active_resources=4)
        session = PaneResourceSession(
            layout.schedule, groups, owners, leases,
            source_identity_keys={endpoint.source_id: stable_identity_key(endpoint.source_id)
                                  for item in groups for endpoint in item.endpoints},
        )
        preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
        queue = PaneFairDeliveryQueue(tuple(name for name in names))
        pump = PaneResourcePump(session, layout, preparer, queue, poll_interval_s=0.004)
        return layout, session, leases, owners, queue, pump

    def test_three_independent_resources_fourth_empty_stop_selected_and_all(self) -> None:
        layout, session, leases, owners, queue, pump = self.make_plan()
        self.assertEqual(layout.empty_slots, (4,))
        with self.assertRaisesRegex(RuntimeError, "applied session"):
            pump.activate()
        self.assertEqual(session.preview()[0].affected_pane_ids, ("a",))
        session.apply()
        self.assertEqual(leases.active_resource_count, 3)
        pump.activate()
        try:
            futures = [pump.start_resource(resource_id) for resource_id in owners]
            for future in futures:
                self.assertIsNotNone(future.result(timeout=3).capture_id)
            self.assertEqual(session.active_resource_count, 3)
            for index, owner in enumerate(owners.values(), 1):
                start = (100e6, 140e6, 200e6)[index - 1]
                owner.publish(frame(owner.admission_source_id, 7, start, start + 8e6))
            self.wait_until(lambda: queue.pending_count == 3)
            delivered = queue.drain()
            self.assertEqual(tuple(item.delivery.pane_id for item in delivered), ("a", "b", "c"))
            self.assertEqual(tuple(item.bundle.unit for item in delivered), ("dBFS/bin",) * 3)
            impact, stopped = pump.stop_selected("b")
            self.assertEqual(impact, ("b",))
            stopped.result(timeout=3)
            self.assertEqual(session.active_resource_count, 2)
            self.assertTrue(owners["device-1"].running and owners["device-3"].running)
            self.assertFalse(owners["device-2"].running)
            self.assertEqual(leases.active_resource_count, 2)
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        self.assertEqual(leases.active_resource_count, 0)
        self.assertTrue(all(item.phase is PanePumpPhase.STOPPED for item in pump.snapshot()))

    def test_partial_thread_launch_failure_releases_all_prestart_leases(self) -> None:
        _, session, leases, owners, _, pump = self.make_plan()
        session.apply()
        original_launch = _ResourceWorker.launch

        def launch_or_fail(worker: _ResourceWorker) -> None:
            if worker.resource.physical_stream_resource_id == "device-2":
                raise RuntimeError("test-only Thread.start refusal")
            original_launch(worker)

        with patch.object(_ResourceWorker, "launch", launch_or_fail):
            with self.assertRaisesRegex(RuntimeError, "all leases released"):
                pump.activate()
        self.assertEqual(leases.active_resource_count, 0)
        self.assertEqual(session.active_resource_count, 0)
        self.assertTrue(all(item.phase is PanePumpPhase.STOPPED for item in pump.snapshot()))
        self.assertTrue(all(not owner.running for owner in owners.values()))
        pump.join_after_stop(3)

    def test_shared_resource_requires_impact_ack_and_terminal_before_retune(self) -> None:
        layout, session, leases, owners, queue, pump = self.make_plan(2, time_sliced=True)
        del layout
        owner = owners["device-1"]
        session.apply()
        pump.activate()
        try:
            first = pump.start_resource("device-1").result(timeout=3)
            self.assertEqual(first.slot_index, 0)
            with self.assertRaisesRegex(RuntimeError, "confirm"):
                pump.stop_selected("a")
            self.wait_until(lambda: pump.snapshot()[0].planned_slot_overrun)
            self.assertEqual([event[0] for event in owner.events], ["start"])
            completed = replace(
                terminal(), source_id=owner.admission_source_id, epoch=7,
                frequencies_hz=np.linspace(100e6, 108e6, 4),
            )
            owner.publish(bundle_from_sweep(completed))
            try:
                self.wait_until(lambda: len(owner.events) >= 3)
            except AssertionError:
                self.fail(f"no scheduled advance: {pump.snapshot()!r}, {owner.events!r}, "
                          f"rejected={session.rejected_publications('device-1')}, queue={queue.metrics()!r}")
            self.assertEqual([event[0] for event in owner.events[:3]], ["start", "stop", "start"])
            self.assertEqual(pump.snapshot()[0].activation.slot_index, 1)
            self.assertEqual(queue.pending_count, 1)
            impact, stopped = pump.stop_selected("a", acknowledge_shared=True)
            self.assertEqual(impact, ("a", "b"))
            stopped.result(timeout=3)
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        self.assertEqual(leases.active_resource_count, 0)
        self.assertEqual(queue.pending_count, 0)

    def test_poll_failure_retains_owner_until_explicit_stop(self) -> None:
        _, session, leases, owners, _, pump = self.make_plan(1)
        owner = owners["device-1"]
        session.apply()
        pump.activate()
        try:
            pump.start_resource("device-1").result(timeout=3)
            owner.fail_poll = True
            self.wait_until(lambda: pump.snapshot()[0].phase is PanePumpPhase.STOP_REQUIRED)
            self.assertIsNone(pump.snapshot()[0].activation)
            self.assertEqual(leases.active_resource_count, 1)
            self.assertTrue(owner.running)
            pump.stop_resource("device-1").result(timeout=3)
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        self.assertEqual(leases.active_resource_count, 0)

    def test_rtbw_time_slice_waits_for_first_admitted_frame(self) -> None:
        _, session, leases, owners, queue, pump = self.make_plan(
            2, time_sliced=True, mode=CaptureMeasurementMode.RTBW)
        owner = owners["device-1"]
        owner.admission_mode = "rtbw"
        session.apply()
        pump.activate()
        try:
            first = pump.start_resource("device-1").result(timeout=3)
            self.assertEqual(first.slot_index, 0)
            self.wait_until(lambda: pump.snapshot()[0].planned_slot_overrun)
            self.assertEqual([event[0] for event in owner.events], ["start"])
            owner.publish(live_frame(owner.admission_source_id,
                                     owner.admission_session_id, 7))
            self.wait_until(lambda: len(owner.events) >= 3)
            self.assertEqual([event[0] for event in owner.events[:3]], ["start", "stop", "start"])
            self.assertEqual(pump.snapshot()[0].activation.slot_index, 1)
            self.assertEqual(queue.pending_count, 1)
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        self.assertEqual(leases.active_resource_count, 0)

    def test_failed_stop_keeps_claim_and_requires_explicit_retry(self) -> None:
        _, session, leases, owners, _, pump = self.make_plan(1)
        owner = owners["device-1"]
        session.apply()
        pump.activate()
        try:
            pump.start_resource("device-1").result(timeout=3)
            owner.fail_stop = True
            with self.assertRaisesRegex(RuntimeError, "Stop did not confirm"):
                pump.stop_resource("device-1").result(timeout=3)
            self.assertEqual(pump.snapshot()[0].phase, PanePumpPhase.STOP_REQUIRED)
            self.assertIsNone(pump.snapshot()[0].activation)
            self.assertEqual(leases.active_resource_count, 1)
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                pump.join_after_stop(0.1)
            owner.fail_stop = False
            pump.stop_resource("device-1").result(timeout=3)
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        self.assertEqual(leases.active_resource_count, 0)

    def test_stop_without_start_releases_lease_without_opening_receiver(self) -> None:
        _, session, leases, owners, _, pump = self.make_plan(1)
        session.apply()
        pump.activate()
        stopping = pump.stop_resource("device-1")
        self.assertFalse(stopping.cancel())
        stopping.result(timeout=3)
        pump.join_after_stop(3)
        self.assertEqual(owners["device-1"].events, [])
        self.assertEqual(leases.active_resource_count, 0)

    def test_future_cancellation_cannot_detach_an_accepted_start(self) -> None:
        _, session, leases, _, _, pump = self.make_plan(1)
        session.apply()
        pump.activate()
        try:
            starting = pump.start_resource("device-1")
            self.assertFalse(starting.cancel())
            self.assertEqual(starting.result(timeout=3).slot_index, 0)
        finally:
            for future in pump.stop_all().values():
                future.result(timeout=3)
            pump.join_after_stop(3)
        self.assertEqual(leases.active_resource_count, 0)

    def test_pump_refuses_mismatched_compiled_plan_before_threads(self) -> None:
        layout, session, _, _, queue, _ = self.make_plan(1)
        altered_layout = compile_pane_layout(
            (PaneLayoutSlot(1, pane("a", "rx-1", 100e6, 108e6,
                                    ReceiverBindingMode.DEDICATED_PARALLEL)),),
            (group("device-1", "rx-1"),), {"capture": profile(20e6)},
        )
        preparer = PaneDeliveryPreparer(altered_layout, (group("device-1", "rx-1"),),
                                         PresentationAllocationBudget())
        self.assertIsNot(layout.schedule, altered_layout.schedule)
        with self.assertRaisesRegex(ValueError, "exact compiled"):
            PaneResourcePump(session, altered_layout, preparer, queue)


if __name__ == "__main__":
    unittest.main()
