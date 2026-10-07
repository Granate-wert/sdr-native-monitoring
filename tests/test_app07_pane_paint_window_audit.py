"""Offline synthetic window evidence; no hardware/render rate claim."""
from dataclasses import replace
import unittest

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView
from sdr_monitor.domain.pane_paint_statistics import summarize_pane_paint_snapshot
from sdr_monitor.services.pane_paint_diagnostics import PanePaintDiagnosticObservation
from sdr_monitor.services.pane_paint_window_audit import (
    AdmissionWindowCohort as Admission, PaintWindowCohort as Cohort, audit_pane_paint_window,
)
from tests.test_app07_pane_paint_statistics import distinct_event, snapshot_with
from tests.test_app07_pane_paint_timing import paint_event


class PaintWindowAuditTests(unittest.TestCase):
    def setUp(self):
        self.original = paint_event(before=2200, after=2250, recorded=3500)
        self.clock = self.original.host_clock
        self.graph = self.original.ref.graph_instance_id

    def event(self, sequence, before=2200, after=2250):
        event = distinct_event(self.original, sequence)
        return replace(event, paint_return=replace(event.paint_return,
            before_paint_ns=before, sampled_after_return_ns=after))

    def observation(self, events=(), before=4000, after=4010, **changes):
        if not events:
            changes.setdefault('graph_instance_id', self.graph)
        snapshot = snapshot_with(*events, **changes)
        return PanePaintDiagnosticObservation(before, after, self.clock, snapshot,
                                              summarize_pane_paint_snapshot(snapshot))

    def audit(self, *tail):
        return audit_pane_paint_window((self.observation(before=1000, after=1010), *tail),
            window_start_ns=2000, window_end_ns=3000, host_clock=self.clock)

    def test_repeated_snapshots_do_not_double_count(self):
        event = self.event(1)
        result = self.audit(self.observation((event,)), self.observation((event,), before=5000, after=5010))
        self.assertEqual((result.unique_events, result.repeated_event_observations), (1, 1))
        self.assertEqual(result.inside_paint_statistics[0].paint_returns, 1)
        self.assertFalse(result.lifetime_complete)

    def test_all_boundary_cohorts_and_unknown_are_explicit(self):
        ranges = ((1800,1900), (2200,2250), (1900,2100), (2900,3100),
                  (1900,3100), (3100,3200), (2000,2000), (2990,3000))
        events = tuple(self.event(i + 1, *pair) for i, pair in enumerate(ranges))
        events += (replace(self.event(9), paint_return=None),)
        counts = dict(self.audit(self.observation(events)).paint_cohorts)
        self.assertEqual(counts, {Cohort.BEFORE:1, Cohort.INSIDE:2, Cohort.CROSS_START:1,
            Cohort.CROSS_END:2, Cohort.CROSS_BOTH:1, Cohort.AFTER:1, Cohort.UNKNOWN:1})

    def test_admission_is_ledger_stamp_not_exact_native_time(self):
        events = []
        for i, stamp in enumerate((1800,2180,3050), 1):
            paint = replace(self.event(i), sequence=2*i)
            events += [replace(paint, sequence=2*i-1, stage=Stage.ADMITTED,
                               host_perf_ns=stamp, paint_return=None), paint]
        result = self.audit(self.observation(tuple(events)))
        self.assertEqual(dict(result.inside_paint_admission_cohorts),
                         {Admission.BEFORE:1, Admission.INSIDE:1, Admission.AFTER:1})

    def test_missing_admission_and_event_ids_not_invented_as_loss(self):
        result = self.audit(self.observation((self.event(2), self.event(5))))
        self.assertEqual(result.unobserved_ids_between_retained_events, 2)
        self.assertEqual(dict(result.inside_paint_admission_cohorts), {Admission.UNKNOWN:2})

    def test_counter_deltas_cover_observations_not_exact_window(self):
        result = self.audit(self.observation(event_evictions=5))
        self.assertEqual(dict(result.observation_counter_deltas)['event_evictions'], 5)
        self.assertEqual(result.observation_envelope_ns, (1000,4010))
        self.assertEqual(result.maximum_query_elapsed_ns, 10)

    def test_changed_same_event_refused(self):
        event = self.event(1)
        with self.assertRaisesRegex(ValueError, 'changed its original evidence'):
            self.audit(self.observation((event,)),
                       self.observation((replace(event, host_perf_ns=3501),), before=5000, after=5010))

    def test_foreign_graph_refused(self):
        with self.assertRaisesRegex(ValueError, 'different graphs'):
            self.audit(self.observation(graph_instance_id='foreign'))

    def test_future_ledger_stamp_refused(self):
        with self.assertRaisesRegex(ValueError, 'stamp contradicts'):
            self.audit(self.observation((replace(self.event(1), host_perf_ns=5000),)))

    def test_mismatched_summary_refused(self):
        observation = self.observation((self.event(1),))
        with self.assertRaisesRegex(ValueError, 'summary does not belong'):
            self.audit(replace(observation, summary=self.observation().summary))

    def test_regressed_counter_refused(self):
        with self.assertRaisesRegex(ValueError, 'counters regressed'):
            self.audit(self.observation(event_drops=2), self.observation(before=5000, after=5010))

    def test_late_admission_refused(self):
        paint = self.event(1)
        admission = replace(paint, sequence=2, stage=Stage.ADMITTED, paint_return=None)
        with self.assertRaisesRegex(ValueError, 'admission event follows'):
            self.audit(self.observation((paint, admission)))

    def test_invalid_window_and_unbracketed_window_refused(self):
        observations = (self.observation(),)
        for start,end in ((True,3000),(3000,2000),(2000,3000)):
            with self.subTest(start=start,end=end), self.assertRaises(ValueError):
                audit_pane_paint_window(observations, window_start_ns=start,
                                       window_end_ns=end, host_clock=self.clock)

    def test_unknown_observation_clock_and_overlap_refused(self):
        with self.assertRaises(ValueError):
            self.audit(replace(self.observation(), observation_clock=None))
        with self.assertRaises(ValueError):
            self.audit(self.observation(), self.observation(before=4005, after=4020))

    def test_changed_obligation_identity_across_stages_refused(self):
        paint = self.event(1)
        admission = replace(paint, sequence=2, stage=Stage.ADMITTED, paint_return=None,
                            ref=replace(paint.ref, view=PaneDeliveryView.WATERFALL))
        with self.assertRaisesRegex(ValueError, 'changed its original identity'):
            self.audit(self.observation((paint, admission)))

    def test_observation_capacity_and_mutable_input_refused(self):
        observation = self.observation()
        for observations in ([observation], (observation,) * 65, ()):
            with self.subTest(count=len(observations)), self.assertRaises(ValueError):
                audit_pane_paint_window(observations, window_start_ns=2000,
                                       window_end_ns=3000, host_clock=self.clock)

    def test_future_paint_receipt_refused(self):
        paint = self.event(1, after=4500)
        with self.assertRaisesRegex(ValueError, 'paint return occurred after'):
            self.audit(self.observation((paint,)))
