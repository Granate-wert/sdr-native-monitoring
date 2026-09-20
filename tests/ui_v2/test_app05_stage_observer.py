"""Paired stage timings never borrow another source or projection's stamps."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("stage_observer",
    Path(__file__).resolve().parents[2] / "scripts/profile_app05_rtbw_stages.py")
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)


def request(token=1):
    frame = SimpleNamespace(timestamp_ns=token, config_generation=1)
    return SimpleNamespace(traces=(("current", SimpleNamespace(source_frame=frame)),),
                           generation=1, viewport=(0., 1., 100))


class StageObserverTests(unittest.TestCase):
    def test_gui_cpu_totals_survive_eviction_and_do_not_measure_other_threads(self):
        rows = OBSERVER.GuiIntervals(capacity=1)
        measured = rows.wrap(lambda value: "paint", lambda value: value)
        with patch.object(OBSERVER, "monotonic_ns", side_effect=(0, 2_000_000, 3_000_000, 7_000_000)), \
             patch.object(OBSERVER, "thread_time", side_effect=(0, .001, .002, .005)):
            self.assertEqual(measured(1), 1)
            self.assertEqual(measured(2), 2)
        self.assertEqual(rows.evictions, 1)
        self.assertEqual(rows.totals["paint"], dict(calls=2, wall_ms=6., cpu_ms=4.))
        rows.gui_thread = -1
        with patch.object(OBSERVER, "monotonic_ns") as clock:
            self.assertEqual(measured(3), 3)
        clock.assert_not_called()
        self.assertEqual(rows.totals["paint"]["calls"], 2)

    def test_gui_intervals_keep_exact_nested_boundaries_with_bounded_scalar_rows(self):
        rows = OBSERVER.GuiIntervals(capacity=2)
        nested = rows.wrap("nested", lambda value: value)
        outer = rows.wrap("outer", lambda value: nested(value))
        with patch.object(OBSERVER, "monotonic_ns", side_effect=(1, 2, 3, 4, 5, 6)):
            self.assertEqual(outer(17), 17)
            self.assertEqual(nested(18), 18)
        self.assertEqual(list(rows.rows), [dict(stage="outer", begin_ns=1, end_ns=4),
                                         dict(stage="nested", begin_ns=5, end_ns=6)])
        self.assertEqual(rows.evictions, 1)

    def test_service_nested_costs_are_exclusive_and_bounded_without_losing_totals(self):
        costs = OBSERVER.ServiceCosts(capacity=2)
        inner = costs.wrap("inner", lambda value: value, lambda value: value)
        outer = costs.wrap("outer", lambda value: inner(value), lambda value: value)
        with patch.object(OBSERVER, "perf_counter", side_effect=(0, 1, 3, 5, 10, 11)), \
             patch.object(OBSERVER, "thread_time", side_effect=(0, 1, 2, 3, 4, 5)):
            self.assertEqual(outer(7), 7)
            self.assertEqual(inner(8), 8)
        self.assertEqual(costs.totals["outer"]["elapsed_ms"], 5000)
        self.assertEqual(costs.totals["outer"]["exclusive_ms"], 3000)
        self.assertEqual(costs.totals["inner"]["exclusive_ms"], 3000)
        self.assertEqual(costs.totals["inner"]["calls"], 2)
        self.assertEqual(costs.totals["outer"]["cpu_ms"], 3000)
        self.assertEqual(costs.totals["outer"]["exclusive_cpu_ms"], 2000)
        self.assertEqual(costs.evictions, 1)
        self.assertEqual(len(costs.rows), 2)

    def test_service_failure_keeps_accounting_and_clears_nesting(self):
        costs = OBSERVER.ServiceCosts()
        def fail():
            raise ValueError("expected")
        with patch.object(OBSERVER, "perf_counter", side_effect=(1, 2)):
            with self.assertRaisesRegex(ValueError, "expected"):
                costs.wrap("failed", fail)()
        self.assertEqual(costs.local.stack, [])
        self.assertEqual(costs.totals["failed"]["errors"], 1)
        self.assertEqual(costs.rows[0]["outcome"], "ValueError")

    def test_wrapped_bound_operation_is_identifiable_at_actual_submit(self):
        class Owner:
            def _prepare(self, snapshot):
                return snapshot

        owner, snapshot = Owner(), object()
        submitted, prepared, cpu = [], [], []
        original_submit = ThreadPoolExecutor.submit

        def before_submit(executor, operation, *args, **kwargs):
            if isinstance(getattr(operation, "__self__", None), Owner) and operation.__name__ == "_prepare":
                submitted.append(args[0])

        with patch.object(Owner, "_prepare", OBSERVER.instrumented_call(
                Owner._prepare, before=lambda _, value: prepared.append(value), cpu_samples=cpu)), \
                patch.object(ThreadPoolExecutor, "submit", OBSERVER.instrumented_call(
                    original_submit, before=before_submit)), ThreadPoolExecutor(max_workers=1) as executor:
            self.assertIs(executor.submit(owner._prepare, snapshot).result(timeout=2), snapshot)
            self.assertIs(owner._prepare.__self__, owner)
        self.assertEqual(submitted, [snapshot])
        self.assertEqual(prepared, [snapshot])
        self.assertEqual(len(cpu), 1)

    def frame_stages(self, records, identity=(1, "rtbw", 1)):
        for when, name in enumerate(("publish", "offer", "coalesced", "prepare_begin", "prepare_end", "delivered")):
            records.mark(identity, name, when)

    def test_exact_accepted_projection_and_first_frame_stamps_form_paired_durations(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        old, accepted = request(), request()
        with patch.object(OBSERVER, "perf_counter", side_effect=(600, 6, 7, 8, 9)):
            records.request(old, "projection_offer")
            for name in ("projection_offer", "projection_begin", "projection_end"):
                records.request(accepted, name)
            records.accepted(accepted)
        records.painted((1, "rtbw", 1), 10)
        row = records.rows[0]
        self.assertEqual(row["total"], 10000)
        self.assertEqual(row["projection_offer"], 1000)
        self.assertEqual(sum(v for k, v in row.items() if k not in ("token", "total")), row["total"])
        self.assertEqual((records.missing, records.reordered), (0, 0))

    def test_missing_and_reordered_samples_are_reported_not_filled(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        records.painted((2, "rtbw", 1), 10)
        self.assertEqual(records.missing, 1)
        for when, name in enumerate(("projection_offer", "projection_begin", "projection_end", "applied"), 6):
            records.mark((1, "rtbw", 1), name, when)
        records.painted((1, "rtbw", 1), 8)
        self.assertEqual(records.reordered, 1)
        self.assertEqual(list(records.rows), [])

    def test_early_required_paint_does_not_wait_for_optional_completion(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        value = request()
        with patch.object(OBSERVER, "perf_counter", side_effect=(6, 7, 8, 9, 10, 20)):
            for name in ("projection_offer", "projection_begin", "required_ready", "required_callback"):
                records.request(value, name)
            records.accepted(value, required_only=True)
            records.request(value, "projection_end")
        records.painted((1, "rtbw", 1), 11)
        self.assertEqual((records.missing, records.reordered), (0, 0))
        self.assertEqual(records.rows[0]["projection_end"], 1000)
        self.assertEqual(records.details[0]["completion_kind"], "required-stage")
        self.assertEqual(records.accepted_requests[(1, "rtbw", 1)]["projection_callback"], 9)

    def test_early_missing_ready_cannot_borrow_final_completion(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        value = request()
        with patch.object(OBSERVER, "perf_counter", side_effect=(6, 7, 8, 9)):
            for name in ("projection_offer", "projection_begin", "projection_end"):
                records.request(value, name)
            records.accepted(value, required_only=True)
        records.painted((1, "rtbw", 1), 10)
        self.assertEqual(records.missing_stages["projection_end"], 1)
        self.assertFalse(records.rows)

    def test_density_only_never_overwrites_required_admission_or_flow(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        required, density = request(), request()
        density.required_work = False
        with patch.object(OBSERVER, "perf_counter", side_effect=(6, 7, 8, 9, 10, 11)):
            for name in ("projection_offer", "projection_begin", "projection_end"):
                records.request(required, name)
            records.accepted(required)
            records.request(density, "projection_offer")
            records.request(density, "projection_begin")
            records.accepted(density)
        records.painted((1, "rtbw", 1), 12)
        self.assertEqual(records.accepted_requests[(1, "rtbw", 1)]["applied"], 9)
        self.assertEqual(records.projection_events["projection_begin:density_only"], 1)
        only_density = request(2)
        only_density.required_work = False
        records.request(only_density, "projection_begin")
        records.accepted(only_density)
        self.assertNotIn((2, "rtbw", 1), records.flow)

    def test_flow_is_bounded_and_dispatch_keeps_actual_start_time(self):
        records = OBSERVER.StageRecords(2)
        for token in range(4):
            records.mark((token, "rtbw", 1), "publish", token)
        self.assertEqual(len(records.flow), 2)
        self.assertEqual(records.flow_evictions, 2)
        value = request(3)
        records.request(value, "projection_dispatch", 123)
        row = next(iter(records.requests.values()))
        self.assertEqual(row["projection_dispatch"], 123)

    def test_history_is_bounded_and_evicted_identity_is_missing(self):
        records = OBSERVER.StageRecords(2)
        for token in range(10):
            records.mark((token, "rtbw", 1), "publish", token)
            records.request(request(token), "projection_offer")
        self.assertEqual(len(records.frames), 2)
        self.assertEqual(len(records.requests), 2)
        records.painted((0, "rtbw", 1), 10)
        self.assertEqual(records.missing, 1)

    def test_source_reoffer_distinguishes_viewport_and_generation(self):
        records = OBSERVER.StageRecords()
        first = request()
        records.request(first, "projection_offer")
        records.request(first, "projection_offer")
        changed = request()
        changed.viewport = (0., 2., 100)
        records.request(changed, "projection_offer")
        changed.generation = 2
        records.request(changed, "projection_offer")
        records.request(request(2), "projection_offer")
        self.assertEqual(records.projection_events["same_source"], 3)
        self.assertEqual(records.projection_events["same_source_generation_viewport"], 1)
        self.assertEqual(records.projection_events["same_source_new_viewport"], 1)
        self.assertEqual(records.projection_events["new_source"], 1)

    def test_request_keeps_its_delivery_when_same_source_is_prepared_again(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        old = request()
        with patch.object(OBSERVER, "perf_counter", side_effect=(6, 7, 8, 9)):
            records.request(old, "projection_offer")
            records.mark((1, "rtbw", 1), "coalesced", 20)
            records.mark((1, "rtbw", 1), "prepare_begin", 21)
            records.mark((1, "rtbw", 1), "prepare_end", 22)
            records.mark((1, "rtbw", 1), "delivered", 23)
            records.request(old, "projection_begin")
            records.request(old, "projection_end")
            records.accepted(old)
        records.painted((1, "rtbw", 1), 10)
        self.assertEqual(records.rows[0]["prepare_begin"], 1000)
        self.assertEqual(records.frames[(1, "rtbw", 1)]["prepare_begin"], 21)
        self.assertEqual(records.reordered, 0)

    def test_incomplete_new_viewport_cannot_borrow_previous_accepted_stages(self):
        records = OBSERVER.StageRecords()
        self.frame_stages(records)
        first, second = request(), request()
        second.viewport = (0., 2., 100)
        with patch.object(OBSERVER, "perf_counter", side_effect=(6, 7, 8, 9, 10, 11)):
            for stage in ("projection_offer", "projection_begin", "projection_end"):
                records.request(first, stage)
            records.accepted(first)
            records.request(second, "projection_offer")
            records.accepted(second)
        records.painted((1, "rtbw", 1), 12)
        self.assertEqual(records.missing, 1)
        self.assertFalse(records.rows)

    def test_navigation_and_details_are_bounded_scalar_witnesses(self):
        records = OBSERVER.StageRecords(2)
        for token in range(5):
            self.frame_stages(records, (token, "rtbw", 1))
            value = request(token)
            with patch.object(OBSERVER, "perf_counter", side_effect=(6, 7, 8, 9)):
                for stage in ("projection_offer", "projection_begin", "projection_end"):
                    records.request(value, stage)
                records.accepted(value)
            records.visibility(False, 8)
            records.visibility(True, 9)
            records.visibility(True, 9.5)  # redundant signal must not reset show time
            records.viewport((0., 1., 100), 8)
            records.painted((token, "rtbw", 1), 10)
        self.assertEqual(len(records.accepted_requests), 2)
        self.assertEqual(len(records.details), 2)
        detail = records.details[-1]
        self.assertEqual(detail["identity"], (4, "rtbw", 1))
        self.assertEqual(detail["since_show_ms"], 1000)
        self.assertEqual(detail["since_viewport_ms"], 2000)
        self.assertEqual(detail["geometry"], (1, (0., 1., 100)))

    def test_running_preparation_survives_same_source_coalescing_and_control_ack_wrapper(self):
        records = OBSERVER.StageRecords(2)
        value = request()
        identity = (1, "rtbw", 1)
        for when, name in enumerate(("publish", "offer", "coalesced")):
            records.mark(identity, name, when)
        packet = SimpleNamespace(snapshot=value.traces[0][1].source_frame, value=object())
        with patch.object(OBSERVER, "perf_counter", side_effect=(3, 4, 5, 6, 7, 8, 9)):
            started = records.preparation_started(identity)
            records.mark(identity, "coalesced", 3.5)  # newer pending tick, same source
            records.preparation_finished(packet, started)
            records.delivered(SimpleNamespace(snapshot=packet.snapshot, value=packet.value))
            for stage in ("projection_offer", "projection_begin", "projection_end"):
                records.request(value, stage)
            records.accepted(value)
        records.painted(identity, 10)
        self.assertEqual(records.missing, 0)
        self.assertEqual(records.rows[-1]["prepare_begin"], 1000)
        for token in range(3, 10):
            new_packet = SimpleNamespace(snapshot=SimpleNamespace(timestamp_ns=token, config_generation=1), value=object())
            records.preparation_finished(new_packet, {})
            records.delivered(new_packet)
        self.assertEqual(len(records.prepared_deliveries), 2)
        self.assertEqual(len(records.deliveries), 2)


if __name__ == "__main__":
    unittest.main()
