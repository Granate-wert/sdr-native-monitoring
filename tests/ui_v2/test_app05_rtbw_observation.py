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
