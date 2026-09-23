"""RTBW age observes uploaded/displayed publications, not newer hidden buffers."""
import importlib.util
from dataclasses import replace
from concurrent.futures import Future
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("rtbw_observer", ROOT / "scripts/benchmark_app05_rtbw_observation.py")
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)


def pane(token=3, uploads=1, visible=True, generation=7):
    return SimpleNamespace(image_items=[SimpleNamespace(isVisible=lambda: visible)],
        metrics=SimpleNamespace(image_uploads=uploads),
        _renderer=SimpleNamespace(timestamps_ns=lambda: np.array([1, token], dtype=np.int64)),
        grid_signature=SimpleNamespace(configuration_generation=generation))


class RtbwUploadWitnessTests(unittest.TestCase):
    def test_matched_persistence_reverse_keeps_mode_identity_and_contiguous_middle(self):
        forward = OBSERVER.matched_persistence_order(False)
        reverse = OBSERVER.matched_persistence_order(True)
        self.assertEqual(forward, ("A1", "B1", "B2", "A2"))
        self.assertEqual(reverse, ("B1", "A1", "A2", "B2"))
        self.assertEqual(sorted(forward), sorted(reverse))
        self.assertEqual(tuple(name[0] for name in reverse), ("B", "A", "A", "B"))

    def test_stage_timing_excludes_work_crossing_steady_boundaries(self):
        from collections import deque
        samples = {"density": deque(maxlen=2)}
        overflow = {"density": 0}
        self.assertFalse(OBSERVER.record_bounded_stage_interval(
            samples, overflow, "density", 1.0, 0.8, 1.1))
        self.assertTrue(OBSERVER.record_bounded_stage_interval(
            samples, overflow, "density", 1.0, 1.0, 1.2))
        self.assertTrue(OBSERVER.record_bounded_stage_interval(
            samples, overflow, "density", 1.0, 1.4, 1.8))
        durations = list(samples["density"])
        self.assertEqual(len(durations), 2)
        self.assertAlmostEqual(durations[0], 200)
        self.assertAlmostEqual(durations[1], 400)
        self.assertTrue(OBSERVER.record_bounded_stage_interval(
            samples, overflow, "density", 1.0, 1.8, 1.9))
        self.assertEqual(overflow["density"], 1)
        self.assertEqual(len(samples["density"]), 2)
        report = OBSERVER.bounded_stage_report(samples, overflow, lambda values: {"p95": max(values)})
        self.assertEqual(report["density"], {"count": 2, "dropped": 1, "duration_ms": None})

    def test_persistence_catchup_requires_exact_uploaded_latest_and_quiescence(self):
        settled = dict(last_viewmodel_update=12, latest_density_update_sequence=12,
            uploaded_density_update_sequence=12, latest_view_is_latest_accepted_density=True,
            latest_view_is_uploaded=True, worker_request_pending=False, pending_view=False)
        self.assertTrue(OBSERVER.persistence_caught_up(settled, 12))
        for changes in (
            {"last_viewmodel_update": 11},
            {"latest_density_update_sequence": 11},
            {"uploaded_density_update_sequence": 11},
            {"latest_view_is_latest_accepted_density": False},
            {"latest_view_is_uploaded": False},
            {"worker_request_pending": True},
            {"pending_view": True},
        ):
            with self.subTest(changes=changes):
                state = settled | changes
                self.assertFalse(OBSERVER.persistence_caught_up(state, 12))

    def test_persistence_catchup_gate_rejects_missing_or_stale_pulse_evidence(self):
        pulse = dict(deadline_met=True, no_stale_upload_observed=True,
            source_publications_during_drain=0, persistence_updates_during_drain=0,
            target_update_sequence=12, accepted_update_sequence_at_end=12,
            latest_view_update_sequence_at_end=12, uploaded_update_sequence_at_end=12,
            final_worker_request_pending=False, final_pending_view=False)
        self.assertTrue(OBSERVER.persistence_catchup_gate_passed([pulse], 1))
        for changes in (
            {"deadline_met": False},
            {"no_stale_upload_observed": None},
            {"source_publications_during_drain": 1},
            {"persistence_updates_during_drain": 1},
            {"uploaded_update_sequence_at_end": 11},
            {"final_worker_request_pending": True},
            {"final_pending_view": True},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(OBSERVER.persistence_catchup_gate_passed([pulse | changes], 1))
        self.assertFalse(OBSERVER.persistence_catchup_gate_passed([], 1))

    def test_persistence_upload_monotonicity_uses_every_commit_and_counter_delta(self):
        uploads = [dict(uploaded_update_sequence=11), dict(uploaded_update_sequence=12)]
        self.assertTrue(OBSERVER.persistence_uploads_monotonic(10, uploads, 12, 2))
        self.assertFalse(OBSERVER.persistence_uploads_monotonic(10,
            [dict(uploaded_update_sequence=12), dict(uploaded_update_sequence=11)], 12, 2))
        self.assertFalse(OBSERVER.persistence_uploads_monotonic(10,
            [dict(uploaded_update_sequence=None)], 12, 1))
        self.assertFalse(OBSERVER.persistence_uploads_monotonic(10, uploads, 12, 1))

    def test_persistence_upload_event_boundary_uses_counter_snapshots(self):
        events = [dict(counter=8), dict(counter=9), dict(counter=10), dict(counter=11)]
        self.assertEqual(OBSERVER.persistence_upload_events_since_counter(events, 8, 10),
            events[1:3])

    def test_persistence_show_requires_exact_latest_and_post_show_image_commits(self):
        settled = dict(presentation_active=True, layer_requested_visible=True,
            image_item_visible=True, last_viewmodel_update=12,
            latest_density_update_sequence=12, uploaded_density_update_sequence=12,
            latest_view_is_latest_accepted_density=True, latest_view_is_uploaded=True,
            worker_request_pending=False, pending_view=False,
            persistence_projector_active=False, persistence_projector_pending=False)
        self.assertTrue(OBSERVER.persistence_rematerialization_settled(settled))
        for changes in (
            {"presentation_active": False},
            {"layer_requested_visible": False},
            {"image_item_visible": False},
            {"uploaded_density_update_sequence": 11},
            {"latest_view_is_latest_accepted_density": False},
            {"worker_request_pending": True},
            {"pending_view": True},
            {"persistence_projector_pending": True},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(OBSERVER.persistence_rematerialization_settled(settled | changes))

        uploads = [dict(counter=5, uploaded_update_sequence=12),
                   dict(counter=6, uploaded_update_sequence=13)]
        self.assertTrue(OBSERVER.persistence_show_uploads_current(uploads, 4, 6, 12))
        self.assertFalse(OBSERVER.persistence_show_uploads_current(uploads[:1], 4, 6, 12))
        self.assertFalse(OBSERVER.persistence_show_uploads_current(
            [uploads[0], uploads[0]], 4, 6, 12))
        self.assertFalse(OBSERVER.persistence_show_uploads_current(
            [uploads[0], dict(counter=6, uploaded_update_sequence=11)], 4, 6, 12))

    def test_fixed_show_target_can_arrive_without_moving_latest_settlement(self):
        state = dict(presentation_active=True, layer_requested_visible=True,
            image_item_visible=True, image_uploads=5,
            last_viewmodel_update=14, latest_density_update_sequence=14,
            uploaded_density_update_sequence=13,
            latest_view_is_latest_accepted_density=True, latest_view_is_uploaded=False,
            worker_request_pending=True, pending_view=True,
            persistence_projector_active=False, persistence_projector_pending=True)
        uploads = [dict(counter=5, uploaded_update_sequence=13, perf_time=10.25)]
        self.assertTrue(OBSERVER.persistence_show_fixed_target_validated(
            state, uploads, 4, 5, 12))
        self.assertFalse(OBSERVER.persistence_rematerialization_settled(state))

    def test_fixed_show_target_revalidates_complete_post_request_event_log(self):
        state = dict(presentation_active=True, layer_requested_visible=True,
            image_item_visible=True, image_uploads=5,
            uploaded_density_update_sequence=13)
        first = dict(counter=5, uploaded_update_sequence=13, perf_time=10.25)
        second = dict(counter=6, uploaded_update_sequence=14, perf_time=10.5)

        def delivered(current_state=state, events=None, end_counter=5, target=12):
            return OBSERVER.persistence_show_fixed_target_validated(
                current_state, [first] if events is None else events,
                4, end_counter, target)

        self.assertTrue(delivered())
        self.assertTrue(delivered(state | {"image_uploads": 6,
            "uploaded_density_update_sequence": 14}, [first, second], 6))
        self.assertFalse(delivered(end_counter=4))  # No post-request commit.
        self.assertFalse(delivered(target=None))
        self.assertFalse(delivered(events=[first | {"uploaded_update_sequence": 11}]))
        self.assertFalse(delivered(events=[first | {"uploaded_update_sequence": None}]))
        self.assertFalse(delivered(state | {"image_item_visible": False}))
        self.assertFalse(delivered(state | {"presentation_active": False}))
        self.assertFalse(delivered(state | {"image_uploads": 6}, [first], 6))
        self.assertFalse(delivered(state | {"image_uploads": 6}, [first, first], 6))
        self.assertFalse(delivered(state | {"image_uploads": 6},
            [first, second | {"counter": 7}], 6))
        self.assertFalse(delivered(state | {"image_uploads": 6,
            "uploaded_density_update_sequence": 12},
            [first, second | {"uploaded_update_sequence": 12}], 6))
        self.assertFalse(delivered(state | {"image_uploads": 6,
            "uploaded_density_update_sequence": 13},
            [first | {"uploaded_update_sequence": 11},
             second | {"uploaded_update_sequence": 13}], 6))
        self.assertFalse(delivered(state | {"image_uploads": 6,
            "uploaded_density_update_sequence": 14},
            [first, second | {"perf_time": 10.0}], 6))

    def test_persistence_page_lifecycle_gate_needs_observed_hide_show_and_stable_restore(self):
        hide = dict(action="hide", visible_state_observed=True,
            before=dict(presentation_active=True, layer_requested_visible=True,
                image_item_visible=True),
            after_visibility_observed=dict(presentation_active=False, image_item_visible=False,
                uploaded_density_update_sequence=None))
        show = dict(action="show", visible_state_observed=True, settled=True,
            before=dict(presentation_active=False, image_item_visible=False,
                uploaded_density_update_sequence=None),
            latest_observed_state=dict(presentation_active=True, layer_requested_visible=True,
                image_item_visible=True, latest_density_update_sequence=12,
                uploaded_density_update_sequence=12),
            stable_samples=3, post_show_request_upload_count=1,
            upload_counter_events_match=True, post_show_uploads_monotonic=True)
        self.assertTrue(OBSERVER.persistence_page_lifecycle_gate_passed([hide, show]))
        self.assertTrue(OBSERVER.persistence_page_lifecycle_gate_passed([hide, show, hide]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed([hide]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed(
            [hide, show | {"settled": False}]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed([show, hide]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed([hide, show, show]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed(
            [hide, show | {"latest_observed_state": show["latest_observed_state"] | {
                "image_item_visible": False}}]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed(
            [hide, show | {"settled": False,
                "fixed_show_target_validated_at_completion": True}]))
        self.assertFalse(OBSERVER.persistence_page_lifecycle_gate_passed([hide, show], overflow=1))

    def test_opt_in_persistence_gate_failure_is_nonzero_after_report_can_be_written(self):
        self.assertEqual(OBSERVER.persistence_catchup_exit_code({}), 0)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_catchup": {"freshness_gate_passed": True}}), 0)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_catchup": {"freshness_gate_passed": False}}), 1)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_catchup": {"freshness_gate_passed": None}}), 1)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_page_lifecycle": {"gate_passed": None}}), 0)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_page_lifecycle": {"gate_passed": True}}), 0)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_page_lifecycle": {"gate_passed": False}}), 1)
        self.assertEqual(OBSERVER.persistence_catchup_exit_code(
            {"persistence_page_lifecycle": {"gate_passed": False,
                "fixed_show_target_validated_at_completion_count": 2}}), 1)

    def test_persistence_page_lifecycle_cli_contract_leaves_room_for_show_deadline(self):
        config = dict(qt_platform="windows", seconds=6.5, page_seconds=2.5,
            cycles=1, persistence_power_bins=32, persistence_display="visual",
            viewport_seconds=0, driver_stop_ms=0, stop_phase="any",
            persistence_abba=False, persistence_catchup=False, memory_seconds=0,
            collect_after_context=False, capture_window=False)
        self.assertIsNone(OBSERVER.persistence_page_lifecycle_config_error(**config))
        for changes in (
            {"qt_platform": "offscreen"},
            {"page_seconds": 1.49},
            {"seconds": 6.49},
            {"page_seconds": 0},
            {"cycles": 2},
            {"persistence_display": "direct"},
            {"persistence_catchup": True},
            {"memory_seconds": .25},
        ):
            with self.subTest(changes=changes):
                self.assertIsNotNone(OBSERVER.persistence_page_lifecycle_config_error(
                    **(config | changes)))

    def test_synthetic_persistence_is_coherent_normalized_owned_and_not_future(self):
        from sdr_monitor.domain.live import LiveSpectrumFrame, LiveSnapshot, LiveSessionState
        from sdr_monitor.domain.analyzer import bundle_from_live
        from sdr_monitor.ui.v2.state.analyzer_layers import persistence_density_from_native
        frame = LiveSpectrumFrame(sequence=11, timestamp_ns=11, source_id="synthetic",
            config_generation=7, center_frequency_hz=100e6, sample_rate_hz=256e3,
            fft_size=256, hop_size=256, frequencies_hz=100e6 + (np.arange(256) - 128) * 1000,
            values=np.full(256, -80, np.float32))
        for count in (1, 4):
            raw = OBSERVER.synthetic_persistence(frame, 32, 3, count)
            other = OBSERVER.synthetic_persistence(frame, 32, 4, count)
            self.assertFalse(np.shares_memory(raw.density, other.density))
            self.assertFalse(raw.density.flags.writeable)
            self.assertFalse(raw.frequencies_hz.flags.writeable)
            self.assertEqual(raw.source_frame_sequence, frame.sequence)
            self.assertEqual(raw.processed_frames, count)
            self.assertEqual(raw.timestamp_quality.value, "unknown")
            normalized = persistence_density_from_native(raw)
            np.testing.assert_array_equal(normalized.density.sum(axis=0), np.ones(256))
            np.testing.assert_array_equal(raw.density.sum(axis=0), np.full(256, count))
            snapshot = LiveSnapshot(generation=7, sequence=11, state=LiveSessionState.RUNNING,
                                    spectrum=frame, persistence=raw)
            bundle = bundle_from_live(snapshot)
            self.assertIs(bundle.persistence, raw)
            self.assertEqual(bundle.coherence_issues, ())
            # A later spectrum can reuse the old histogram, never the reverse.
            self.assertIs(bundle_from_live(replace(snapshot, spectrum=replace(frame, sequence=12))).persistence, raw)
            self.assertIsNone(bundle_from_live(replace(snapshot, spectrum=replace(frame, sequence=10))).persistence)

    def test_persistence_memory_cli_updates_and_uploads_without_forced_collection(self):
        with TemporaryDirectory(prefix="app05-persistence-memory-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", "-X", "faulthandler",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--seconds", "1", "--cycles", "2", "--bins", "4096",
                "--page-seconds", ".3", "--viewport-seconds", ".2", "--memory-seconds", ".25",
                "--persistence-power-bins", "32", "--persistence-every", "10"],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        density = report["persistence"]
        display = report["qt_display_environment"]
        self.assertEqual(display["requested_platform"], "offscreen")
        self.assertEqual(display["actual_platform"], "offscreen")
        self.assertFalse(display["visible_native_window"])
        self.assertEqual(display["requested_window_size_logical"], [1920, 1080])
        self.assertEqual(len(display["spectrum_plot_estimated_physical_size"]), 2)
        self.assertIn("not DWM/compositor scanout", display["scope"])
        self.assertTrue(density["enabled"])
        self.assertGreater(density["accepted"], 2)
        self.assertGreaterEqual(density["generated"], density["accepted"])
        self.assertGreater(density["overlay_metrics"]["image_uploads"], 0)
        self.assertGreater(density["overlay_metrics"]["hidden_updates"], 0)
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["product_imports_outside_checkout"], [])
        self.assertEqual(report["memory"]["after_context_return"]["allocation_budget"]["reserved_bytes"], 0)
        self.assertNotIn("diagnostic_after_collection", report["memory"])

    def test_persistence_abba_rejects_offscreen_before_qt_startup(self):
        with TemporaryDirectory(prefix="app05-abba-validation-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--qt-platform", "offscreen", "--persistence-abba",
                "--persistence-power-bins", "32"], cwd=ROOT,
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--persistence-abba requires --qt-platform windows", result.stderr)

    def test_reverse_order_requires_explicit_matched_run(self):
        with TemporaryDirectory(prefix="app05-baab-validation-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--abba-reverse-order"], cwd=ROOT,
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--abba-reverse-order requires --persistence-abba", result.stderr)

    def test_stage_timing_requires_explicit_matched_run(self):
        with TemporaryDirectory(prefix="app05-stage-validation-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--projection-stage-timing"], cwd=ROOT,
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--projection-stage-timing requires --persistence-abba", result.stderr)

    def test_persistence_catchup_rejects_offscreen_before_qt_startup(self):
        with TemporaryDirectory(prefix="app05-catchup-validation-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--qt-platform", "offscreen", "--persistence-catchup",
                "--persistence-power-bins", "32", "--persistence-display", "visual"], cwd=ROOT,
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--persistence-catchup requires --qt-platform windows", result.stderr)

    def test_stop_phase_preserves_queued_running_done_and_cancelled_without_mutation(self):
        future = Future()
        self.assertEqual(OBSERVER.future_phase(None), "idle")
        self.assertEqual(OBSERVER.future_phase(future), "queued")
        future.set_running_or_notify_cancel()
        self.assertEqual(OBSERVER.future_phase(future), "running")
        future.set_result(None)
        self.assertEqual(OBSERVER.future_phase(future), "done-awaiting-gui")
        cancelled = Future()
        cancelled.cancel()
        self.assertEqual(OBSERVER.future_phase(cancelled), "cancelled-awaiting-gui")
        p = SimpleNamespace(_preparation_future=future, _pending_preparation=object())
        port = SimpleNamespace(_future=None, _pending=None)
        phase = OBSERVER.stop_phase(p, port)
        self.assertTrue(OBSERVER.phase_matches(phase, "preparation:done-awaiting-gui"))
        self.assertFalse(OBSERVER.phase_matches(phase, "idle"))
        self.assertTrue(OBSERVER.phase_matches(phase, "any"))
        self.assertTrue(phase["preparation_pending"])
        self.assertFalse(phase["projection_pending"])

    def test_paint_phase_prioritizes_control_and_labels_resume_window(self):
        for phase in ("starting", "stopping", "idle"):
            self.assertEqual(OBSERVER.paint_phase(phase, False, 1, 2), phase)
        self.assertEqual(OBSERVER.paint_phase("running", False, 1, 2), "hidden")
        self.assertEqual(OBSERVER.paint_phase("running", True, 1, 2), "resume")
        self.assertEqual(OBSERVER.paint_phase("running", True, 2, 2), "steady")

    def test_memory_cli_retains_scalar_phases_and_post_context_observation(self):
        with TemporaryDirectory(prefix="app05-rtbw-memory-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", "-X", "faulthandler",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--seconds", "1", "--cycles", "2", "--bins", "4096",
                "--page-seconds", ".3", "--viewport-seconds", ".2", "--memory-seconds", ".25",
                "--collect-after-context"],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        memory = report["memory"]
        self.assertEqual(report["timing_sample_capacity"], 512)
        self.assertLessEqual(len(memory["samples"]), memory["capacity"])
        self.assertGreaterEqual(memory["total_samples"], len(memory["samples"]))
        self.assertEqual(memory["samples"][0]["label"], "before-start")
        self.assertEqual(memory["samples"][-1]["label"], "after-close")
        self.assertIn("workspace not supplied", memory["samples"][-1]["inventory"]["missing"])
        self.assertEqual(memory["after_context_return"]["allocation_budget"]["reserved_bytes"], 0)
        self.assertEqual(memory["diagnostic_after_collection"]["allocation_budget"]["reserved_bytes"], 0)
        self.assertEqual(memory["diagnostic_after_collection"]["allocation_budget"]["observed_bytes"], 0)
        self.assertFalse(any(memory["diagnostic_after_collection"]["weak_owner_alive"].values()))
        self.assertGreaterEqual(memory["diagnostic_after_collection"]["collected"], 0)
        self.assertEqual(report["remaining_workers"], [])
        for canvas in ("spectrum", "waterfall", "both"):
            self.assertEqual(sum(p["counts"][canvas] for p in report["paint_return_phases"].values()),
                             report["first_paint_publications"][canvas])
        if sys.platform == "win32":
            self.assertTrue(all(s["private_bytes"] > 0 and s["working_set"] > 0 for s in memory["samples"]))

    def test_hidden_admission_does_not_replace_previously_uploaded_token(self):
        previous = OBSERVER.rtbw_key(2, 7)
        newer_ring = pane(token=300, uploads=4)
        self.assertEqual(OBSERVER.uploaded_key(newer_ring, 4, previous), previous)
        self.assertEqual(OBSERVER.uploaded_key(newer_ring, 3, previous), OBSERVER.rtbw_key(300, 7))

    def test_no_visible_image_has_no_paint_key(self):
        self.assertIsNone(OBSERVER.uploaded_key(pane(visible=False), 0, OBSERVER.rtbw_key(3, 7)))

    def test_uploaded_image_without_matching_metadata_is_an_observer_error(self):
        empty = pane()
        empty._renderer.timestamps_ns = lambda: np.empty(0, dtype=np.int64)
        with self.assertRaisesRegex(AssertionError, "without row/grid"):
            OBSERVER.uploaded_key(empty, 0, None)
        empty = pane()
        empty.grid_signature = None
        with self.assertRaises(AssertionError):
            OBSERVER.uploaded_key(empty, 0, None)

    def test_generation_and_unique_token_are_both_part_of_identity(self):
        self.assertNotEqual(OBSERVER.rtbw_key(2, 7), OBSERVER.rtbw_key(2, 8))
        self.assertNotEqual(OBSERVER.rtbw_key(2, 7), OBSERVER.rtbw_key(3, 7))

    def test_cli_actual_composition_churn_and_delayed_stop_keeps_source_active(self):
        with TemporaryDirectory(prefix="app05-rtbw-observer-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", "-X", "faulthandler",
                str(ROOT / "scripts/benchmark_app05_rtbw_observation.py"), "--checkout", str(ROOT),
                "--output", str(output), "--seconds", "1", "--cycles", "2", "--bins", "65536",
                "--page-seconds", ".3", "--viewport-seconds", ".2", "--driver-stop-ms", "100",
                "--stop-phase", "preparation:running"],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["product_imports_outside_checkout"], [])
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["post_close_allocation_budget"]["reserved_bytes"], 0)
        self.assertEqual(report["event_pump"], "QEventLoop.exec")
        self.assertEqual(report["witness_misses"], 0)
        self.assertEqual(report["changed_during_paint"], 0)
        self.assertGreater(report["page_changes"], 0)
        self.assertGreater(report["viewport_changes"], 0)
        self.assertGreater(report["waterfall_metrics"]["hidden_uploads_suppressed"], 0)
        self.assertLessEqual(report["waterfall_rows"], 300)
        self.assertEqual(len(report["controls"]), 2)
        self.assertEqual(report["requested_stop_phase"], "preparation:running")
        for stop in report["controls"]:
            self.assertEqual(stop["phase_at_intent"]["preparation"], "running")
            self.assertTrue(stop["producer_active_at_intent"])
            self.assertTrue(stop["worker_off_gui"])
            self.assertGreater(stop["generated_at_idle"], stop["generated_at_intent"])
            self.assertGreaterEqual(stop["intent_to_idle_ms"], 100)
            self.assertGreaterEqual(stop["intent_to_worker_ms"], 0)
            self.assertGreater(stop["heartbeat_ticks_during_driver_delay"], 0)
            self.assertGreaterEqual(stop["driver_elapsed_ms"], 100)
            self.assertGreaterEqual(stop["driver_return_to_idle_ms"], 0)
            self.assertAlmostEqual(stop["intent_to_idle_ms"], stop["intent_to_worker_ms"]
                + stop["driver_elapsed_ms"] + stop["driver_return_to_idle_ms"], places=5)
            self.assertGreaterEqual(stop["phase_observations"], 1)
        self.assertEqual(sum(p["count"] for p in report["control_by_phase"].values()), 2)
        for name in ("spectrum", "waterfall", "both"):
            self.assertGreater(report["first_paint_publications"][name], 0)
            self.assertGreaterEqual(report["host_publication_to_first_paint_ms"][name]["p50"], 0)


if __name__ == "__main__":
    unittest.main()
