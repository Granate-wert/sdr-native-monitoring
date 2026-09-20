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
