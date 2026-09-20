"""RTBW age observes uploaded/displayed publications, not newer hidden buffers."""
import importlib.util
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
