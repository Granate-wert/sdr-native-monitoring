"""Single-read observation over cached product graph; no physical/Qt proof."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from time import perf_counter_ns
import unittest

from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
from sdr_monitor.services.pane_paint_diagnostics import capture_pane_paint_observation
from tests.test_app07_pane_paint_statistics import snapshot_with
from tests.test_app07_pane_paint_timing import paint_event


class CachedReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.reads = 0

    def pane_delivery_ledger_snapshot(self):
        self.reads += 1
        return self.snapshot


class PaintDiagnosticTests(unittest.TestCase):
    def test_raw_and_summary_use_same_snapshot_single_read(self):
        snapshot = snapshot_with(paint_event())
        reader = CachedReader(snapshot)
        stamps = iter((100, 150))
        observation = capture_pane_paint_observation(reader, now_ns=lambda: next(stamps))
        self.assertEqual(reader.reads, 1)
        self.assertIs(observation.snapshot, snapshot)
        self.assertIs(observation.snapshot.events[0].paint_return, snapshot.events[0].paint_return)
        self.assertEqual(observation.summary.groups[0].base_return.p95_ns, (90, 250))
        self.assertEqual(observation.query_elapsed_ns, 50)
        self.assertIsNone(observation.observation_clock)
        with self.assertRaises(FrozenInstanceError):
            observation.before_capture_ns = 0

    def test_default_clock_origin_is_declared_custom_wrapper_is_unknown(self):
        reader = CachedReader(snapshot_with())
        observed = capture_pane_paint_observation(reader)
        self.assertEqual(observed.observation_clock, HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()))
        self.assertGreaterEqual(observed.query_elapsed_ns, 0)
        self.assertIsNone(capture_pane_paint_observation(reader, now_ns=lambda: perf_counter_ns()).observation_clock)

    def test_observation_clock_never_overwrites_foreign_ledger_clock(self):
        foreign = HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid() + 1)
        snapshot = replace(snapshot_with(), host_clock=foreign)
        observation = capture_pane_paint_observation(CachedReader(snapshot))
        self.assertEqual(observation.snapshot.host_clock, foreign)
        self.assertNotEqual(observation.observation_clock, foreign)

    def test_repeated_overlapping_observations_are_not_accumulated(self):
        reader = CachedReader(snapshot_with(paint_event()))
        first = capture_pane_paint_observation(reader)
        second = capture_pane_paint_observation(reader)
        self.assertEqual(first.summary, second.summary)
        self.assertEqual(second.summary.groups[0].paint_returns, 1)
        self.assertEqual(reader.reads, 2)
        self.assertIs(first.snapshot.events[0], second.snapshot.events[0])
        self.assertFalse(first.summary.lifetime_complete)

    def test_invalid_first_clock_does_not_read_snapshot(self):
        for stamp in (True, -1, 1 << 63, 1.5, None):
            reader = CachedReader(snapshot_with())
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                capture_pane_paint_observation(reader, now_ns=lambda: stamp)
            self.assertEqual(reader.reads, 0)

    def test_second_clock_failure_or_regression_does_not_mutate_ledger(self):
        for end in (99, True, None, 1 << 63):
            snapshot = snapshot_with()
            reader = CachedReader(snapshot)
            stamps = iter((100, end))
            with self.subTest(end=end), self.assertRaises(ValueError):
                capture_pane_paint_observation(reader, now_ns=lambda: next(stamps))
            self.assertEqual(reader.reads, 1)
            self.assertIs(reader.snapshot, snapshot)

    def test_scalar_original_event_evidence_is_json_serializable(self):
        observation = capture_pane_paint_observation(CachedReader(snapshot_with(paint_event())))
        value = json.loads(json.dumps(asdict(observation)))
        self.assertEqual(value["summary"]["groups"][0]["paint_returns"], 1)
        self.assertEqual(value["snapshot"]["events"][0]["ref"]["graph_instance_id"],
                         observation.summary.graph_instance_id)
        self.assertEqual(value["snapshot"]["events"][0]["paint_return"]["sampled_after_return_ns"], 1250)

    def test_actual_applied_session_has_no_owner_activity_from_observation(self):
        from tests.test_app07_pane_resource_session import FakeOwner, PaneResourceSessionTests
        from tests.test_app07_shared_capture_schedule import group, pane
        from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
        fixture = PaneResourceSessionTests()
        fixture.setUp()
        owner = FakeOwner("device")
        session = fixture.session((group("device", "rx"),),
            (pane("one", "rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),), {"device": owner})
        session.apply()
        before = owner.events[:]
        resources = fixture.leases.active_resource_count
        first = session.pane_delivery_ledger_snapshot()
        observed = capture_pane_paint_observation(session)
        self.assertEqual(observed.snapshot, first)
        self.assertEqual(owner.events, before)
        self.assertFalse(owner.running)
        self.assertEqual(fixture.leases.active_resource_count, resources)
        session.stop_all()


if __name__ == "__main__":
    unittest.main()
