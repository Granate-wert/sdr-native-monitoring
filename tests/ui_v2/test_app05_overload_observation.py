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


class PaintAgeWitnessTests(unittest.TestCase):
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
        with TemporaryDirectory(prefix="app05-observer-") as temporary:
            output = Path(temporary) / "result.json"
            result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/benchmark_app04_poll_overload.py"),
                                     "--checkout", str(ROOT), "--output", str(output), "--seconds", "1",
                                     "--bins", "256"], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["product_imports_outside_checkout"], [])
        self.assertIn("QEventLoop.exec", report["event_pump"])
        row = report["results"][0]
        self.assertEqual(row["changed_during_paint"], 0)
        self.assertEqual(row["paint_witness_misses"], 0)
        self.assertEqual(row["terminal_control_gaps"], 1)
        for name in ("spectrum", "waterfall", "both"):
            self.assertGreater(row["first_paint_publications"][name], 0)
            self.assertGreaterEqual(row["host_publication_to_first_paint_return_ms"][name]["p50"], 0)


if __name__ == "__main__":
    unittest.main()
