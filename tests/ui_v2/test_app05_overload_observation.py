"""The performance observer must not manufacture paints or starve Qt cleanup."""
import importlib.util
import gc
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import weakref

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("overload_observer", ROOT / "scripts/benchmark_app04_poll_overload.py")
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)


class PhaseThroughputTests(unittest.TestCase):
    def test_stop_selectors_exclude_overlap_queued_and_done_states(self):
        for poll in ("idle", "running", "queued", "done-awaiting-gui"):
            for projection in ("idle", "running", "queued", "done-awaiting-gui"):
                phase = dict(poll=poll, projection=projection)
                self.assertTrue(OBSERVER.sweep_phase_matches(phase, "any"))
                self.assertEqual(OBSERVER.sweep_phase_matches(phase, "idle"),
                                 poll == projection == "idle")
                self.assertEqual(OBSERVER.sweep_phase_matches(phase, "poll-only"),
                                 poll == "running" and projection == "idle")
                self.assertEqual(OBSERVER.sweep_phase_matches(phase, "projection-only"),
                                 projection == "running" and poll == "idle")
        with self.assertRaises(ValueError):
            OBSERVER.sweep_phase_matches(dict(poll="idle", projection="idle"), "unknown")

    def test_exposure_is_split_without_timer_samples_and_stop_is_terminal(self):
        clock = [0.]
        phases = OBSERVER.PhaseThroughput(lambda: clock[0])
        clock[0] = 1.
        phases.event("delivery")
        phases.transition("hidden")
        clock[0] = 2.
        phases.transition("resume")
        clock[0] = 2.1
        phases.event("partial_spectrum")
        clock[0] = 3.
        phases.transition("stop")
        clock[0] = 3.1
        phases.transition("hidden")
        clock[0] = 3.2
        report = phases.report()
        for name, seconds in dict(startup=.25, hidden=1., resume=.25, steady=1.5, stop=.2).items():
            self.assertAlmostEqual(report[name]["seconds"], seconds)
        self.assertEqual(report["resume"]["events"], {"partial_spectrum": 1})
        self.assertEqual(report["steady"]["events"], {"delivery": 1})
        self.assertAlmostEqual(report["resume"]["events_per_second"]["partial_spectrum"], 4.)

    def test_events_bounded_and_invalid_input_rejected(self):
        clock = [0.]
        phases = OBSERVER.PhaseThroughput(lambda: clock[0])
        for _ in range(10000):
            phases.event("poll_return")
        self.assertEqual(len(phases.counts["startup"]), 1)
        self.assertEqual(phases.report()["startup"]["events_per_second"], {})
        with self.assertRaises(ValueError):
            phases.event("unknown")
        with self.assertRaises(ValueError):
            phases.transition("unknown")
        clock[0] = -1.
        with self.assertRaises(ValueError):
            phases.report()

    def test_first_paint_only_and_missing_keys_do_not_inflate_phase_counts(self):
        phases = OBSERVER.PhaseThroughput(lambda: 0.)
        tracker = OBSERVER.PaintAgeTracker()
        tracker.phases = phases
        key = (1, "partial", 1)
        tracker.publish(key, 1.)
        tracker.painted("spectrum", key, 2.)
        tracker.painted("spectrum", key, 3.)
        tracker.painted("waterfall", key, 4.)
        tracker.painted("waterfall", (2, "partial", 1), 5.)
        self.assertEqual(phases.report()["startup"]["events"], dict(
            spectrum=1, waterfall=1, both=1, partial_spectrum=1, partial_waterfall=1, partial_both=1))


class PaintAgeWitnessTests(unittest.TestCase):
    def test_fixed_visible_target_does_not_borrow_a_lifecycle_pass(self):
        metadata = dict(actual_platform="windows", visible_native_window=True,
                        actual_window_geometry_logical=[7, 30, 1400, 850],
                        screen_device_pixel_ratio=1.75, spectrum_canvas_dpr=1.75,
                        waterfall_canvas_dpr=1.75)
        self.assertTrue(OBSERVER.fixed_visible_target_matches(metadata, (1400, 850), 1.75))
        self.assertFalse(OBSERVER.fixed_visible_target_matches(metadata, (1920, 1080), 1.75))
        self.assertFalse(OBSERVER.fixed_visible_target_matches(metadata, (1400, 850), 1.5))
        self.assertFalse(OBSERVER.fixed_visible_target_matches(
            dict(metadata, actual_platform="offscreen"), (1400, 850), 1.75))

    def test_requested_gate_failure_is_not_a_successful_json_exit(self):
        report = dict(post_close_reserved_bytes=0, remaining_workers=[], results=[
            dict(progressive_paint_gate_passed=False, fixed_target_progressive_gate_passed=False,
                 fixed_visible_target_match=False)])
        self.assertEqual(OBSERVER.requested_gate_failures(report,
            progressive_stage_gate=True, expected_dpr=1.75),
            ["progressive-paint", "fixed-visible-target"])
        self.assertEqual(OBSERVER.requested_gate_failures(report,
            progressive_stage_gate=False, expected_dpr=None), [])
        self.assertEqual(OBSERVER.requested_gate_failures(report,
            progressive_stage_gate=False, expected_dpr=1.75), ["fixed-visible-target"])
        report["post_close_reserved_bytes"] = 1
        self.assertEqual(OBSERVER.requested_gate_failures(report,
            progressive_stage_gate=False, expected_dpr=None), ["cleanup"])

    def test_progressive_pair_requires_both_canvases_same_pass_and_order(self):
        def paint(pane, sequence, state, revision, at, coverage=1):
            return dict(pane=pane, key=[sequence, state, revision],
                        paint_return_s=at, coverage_runs=coverage)

        events = [paint("waterfall", 4, "partial", 1, 1),
                  paint("spectrum", 4, "partial", 1, 2),
                  paint("waterfall", 5, "complete", 0, 3),
                  paint("spectrum", 5, "complete", 0, 4)]
        self.assertIsNone(OBSERVER.paired_progressive_paints(events))
        events.extend((paint("waterfall", 4, "complete", 0, 5),
                       paint("spectrum", 4, "complete", 0, 6)))
        paired = OBSERVER.paired_progressive_paints(events)
        self.assertEqual(paired["sequence"], 4)
        self.assertEqual(set(paired["partial"]), {"spectrum", "waterfall"})
        self.assertEqual(set(paired["complete"]), {"spectrum", "waterfall"})
        self.assertTrue(OBSERVER.progressive_pair_precedes_stop(paired, 7))
        self.assertFalse(OBSERVER.progressive_pair_precedes_stop(paired, 6))
        self.assertFalse(OBSERVER.progressive_pair_precedes_stop(None, 7))
        self.assertIsNone(OBSERVER.paired_progressive_paints(
            [paint("waterfall", 4, "partial", 1, 1), paint("spectrum", 4, "partial", 1, 2),
             paint("waterfall", 4, "complete", 0, 3), paint("spectrum", 4, "complete", 0, 1)]))
        self.assertIsNone(OBSERVER.paired_progressive_paints(
            [paint("waterfall", 4, "partial", 1, 1), paint("spectrum", 4, "partial", 1, 2, 0),
             paint("waterfall", 4, "complete", 0, 3), paint("spectrum", 4, "complete", 0, 4)]))
        self.assertIsNone(OBSERVER.paired_progressive_paints(
            [paint("waterfall", 4, "partial", 1, 1), paint("spectrum", 4, "partial", 2, 2),
             paint("waterfall", 4, "complete", 0, 3), paint("spectrum", 4, "complete", 0, 4)]))

    def test_progressive_pair_latch_survives_raw_trace_eviction(self):
        def paint(pane, sequence, state, revision, at):
            return dict(pane=pane, key=[sequence, state, revision], paint_return_s=at,
                        coverage_runs=1)

        witness = OBSERVER.ProgressivePaintWitness()
        for event in (paint("waterfall", 1, "partial", 1, 1),
                      paint("spectrum", 1, "partial", 1, 2),
                      paint("waterfall", 1, "complete", 0, 3),
                      paint("spectrum", 1, "complete", 0, 4)):
            witness.accept(event)
        self.assertEqual(witness.pair["sequence"], 1)
        for sequence in range(2, 10002):
            witness.accept(paint("spectrum", sequence, "partial", 1, sequence + 4))
        self.assertEqual(witness.pair["sequence"], 1)
        self.assertEqual(len(witness.candidates), 0)
        unmatched = OBSERVER.ProgressivePaintWitness()
        for sequence in range(10000):
            unmatched.accept(paint("spectrum", sequence, "partial", 1, sequence))
        self.assertIsNone(unmatched.pair)
        self.assertLessEqual(len(unmatched.candidates), 64)
        self.assertTrue(all(len(events) <= 16 for events in unmatched.candidates.values()))

    def test_same_pass_partial_and_terminal_do_not_fake_both_paints(self):
        witness = OBSERVER.PaintAgeTracker()
        partial, complete = (1, "partial", 1), (1, "complete", 0)
        witness.publish(partial, 1)
        witness.publish(complete, 2)
        witness.painted("spectrum", partial, 3)
        witness.painted("waterfall", complete, 4)
        self.assertEqual(witness.counts, {"spectrum": 1, "waterfall": 1, "both": 0})
        witness.painted("waterfall", partial, 5)
        self.assertEqual(list(witness.ages["both"]), [4000])
        self.assertEqual(witness.partial_counts["both"], 1)
        self.assertEqual(list(witness.partial_ages["waterfall"]), [4000])
        witness.painted("spectrum", partial, 6)
        self.assertEqual(witness.counts["spectrum"], 1)
        self.assertEqual(witness.repeated, 1)

    def test_capacity_eviction_is_reported_not_assigned_a_new_timestamp(self):
        witness = OBSERVER.PaintAgeTracker(capacity=2)
        for number in range(3):
            key = (number, "partial", 1)
            witness.publish(key, number)
            witness.painted("spectrum", key, number + .1)
        self.assertEqual(len(witness.sources), 2)
        self.assertEqual(len(witness.ages["spectrum"]), 2)
        self.assertEqual(witness.counts["spectrum"], 3)
        witness.painted("waterfall", (0, "partial", 1), 4)
        self.assertEqual(witness.missing, 1)
        self.assertEqual(witness.evicted, 1)
        self.assertEqual(witness.counts["both"], 0)

    def test_duplicate_or_negative_age_is_rejected(self):
        witness = OBSERVER.PaintAgeTracker()
        key = (1, "partial", 1)
        witness.publish(key, 2)
        with self.assertRaises(ValueError):
            witness.publish(key, 3)
        with self.assertRaises(ValueError):
            witness.painted("spectrum", key, 1)
        self.assertEqual(witness.counts["spectrum"], 0)


class RealQtObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_deferred_delete_delivered_in_observation_loop(self):
        owner = QObject()
        gone = []
        owner.destroyed.connect(lambda: gone.append(True))
        owner.deleteLater()
        OBSERVER.run_qt_until(lambda: bool(gone), 1)
        self.assertEqual(gone, [True])

    def test_completed_timeout_and_failed_wait_release_predicate_owner(self):
        class Owner:
            def __init__(self, mode):
                self.mode = mode

            def ready(self):
                if self.mode == "error":
                    raise ValueError("observer failure")
                return self.mode == "complete"

        for mode in ("complete", "timeout", "error"):
            with self.subTest(mode=mode):
                owner = Owner(mode)
                reference = weakref.ref(owner)
                try:
                    OBSERVER.run_qt_until(owner.ready, .02)
                except (TimeoutError, ValueError):
                    pass
                del owner
                gc.collect()  # diagnostic reachability, not a product workaround
                self.assertIsNone(reference(), "stopped observer timer retained its predicate owner")

    def test_timeout_and_callback_error_return_outside_qt(self):
        with self.assertRaises(TimeoutError):
            OBSERVER.run_qt_until(lambda: False, .02)
        def fail():
            raise ValueError("observer predicate")
        with self.assertRaisesRegex(ValueError, "observer predicate"):
            OBSERVER.run_qt_until(fail, 1)
        OBSERVER.run_qt_until(lambda: True, 1)

    def test_actual_composition_cli_records_both_canvases_without_hardware(self):
        self.check_cli()

    def test_sparse_terminal_cli_preserves_domain_identity_and_final_gap(self):
        self.check_cli(terminal_every=100)

    def test_each_grid_reports_post_close_budget(self):
        with TemporaryDirectory(prefix="app05-multigrid-observer-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/benchmark_app04_poll_overload.py"),
                                     "--checkout", str(ROOT), "--output", str(output), "--seconds", "1",
                                     "--bins", "256", "512"],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual([row["bins"] for row in report["results"]], [256, 512])
        self.assertEqual([row["post_close_reserved_bytes"] for row in report["results"]], [0, 0])
        self.assertEqual(report["post_close_reserved_bytes"], 0)

    def test_slow_synthetic_sweep_paints_partial_then_complete_before_stop(self):
        with TemporaryDirectory(prefix="app05-progressive-observer-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/benchmark_app04_poll_overload.py"),
                                     "--checkout", str(ROOT), "--output", str(output), "--seconds", "1",
                                     "--bins", "256", "--producer-phase-ms", "50",
                                     "--progressive-stage-gate"],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        row = report["results"][0]
        self.assertEqual(report["platform"], "Qt offscreen")
        self.assertTrue(row["progressive_paint_gate_passed"])
        self.assertIsNone(row["fixed_target_progressive_gate_passed"])
        self.assertEqual(row["progressive_painted_pair"]["partial"]["spectrum"]["key"][1], "partial")
        self.assertEqual(row["progressive_painted_pair"]["complete"]["spectrum"]["key"][1], "complete")
        self.assertTrue(all(row["progressive_painted_pair"]["complete"][pane]["paint_return_s"]
                            < row["stop_intent_host_s"] for pane in ("spectrum", "waterfall")))
        self.assertGreaterEqual(row["idle_observed_host_s"], row["stop_intent_host_s"])
        self.assertEqual(row["post_stop"]["terminal_key"][1], "gap")
        self.assertEqual(row["post_stop"]["terminal_gap_painted_on"], ["spectrum", "waterfall"])
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["post_close_reserved_bytes"], 0)

    def check_cli(self, terminal_every=1):
        with TemporaryDirectory(prefix="app05-observer-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/benchmark_app04_poll_overload.py"),
                                     "--checkout", str(ROOT), "--output", str(output), "--seconds", "1",
                                     "--bins", "256", "--terminal-every", str(terminal_every)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["product_imports_outside_checkout"], [])
        self.assertIn("QEventLoop.exec", report["event_pump"])
        row = report["results"][0]
        self.assertFalse(report["progressive_stage_gate"])
        self.assertIsNone(row["progressive_paint_gate_passed"])
        self.assertIsNone(row["fixed_target_progressive_gate_passed"])
        self.assertEqual(row["stage_paint_events"], [])
        self.assertEqual(report["terminal_every"], terminal_every)
        if terminal_every > 1:
            self.assertGreater(row["source_superseded"], 0)
        self.assertEqual(row["changed_during_paint"], 0)
        self.assertEqual(row["paint_witness_misses"], 0)
        self.assertEqual(row["terminal_control_gaps"], 1)
        for name in ("spectrum", "waterfall", "both"):
            phases = row["phase_throughput"]
            self.assertEqual(sum(p["events"].get(name, 0) for p in phases.values()),
                             row["first_paint_publications"][name])
            self.assertEqual(sum(p["events"].get("partial_" + name, 0) for p in phases.values()),
                             row["first_partial_paint_publications"][name])
            self.assertGreater(row["first_paint_publications"][name], 0)
            self.assertGreaterEqual(row["host_publication_to_first_paint_return_ms"][name]["p50"], 0)


if __name__ == "__main__":
    unittest.main()
