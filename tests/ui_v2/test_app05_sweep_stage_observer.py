"""Sweep profiling follows exact accepted requests, including page re-projection."""
import importlib.util
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


SHARED = module("sweep_shared_stages", "profile_app05_rtbw_stages.py")
SWEEP = module("sweep_stages", "profile_app05_sweep_tail.py")


class SweepStageObserverTests(unittest.TestCase):
    def test_stale_viewport_cannot_ack_same_already_displayed_source(self):
        source = object()
        request = SimpleNamespace(traces=(("current", SimpleNamespace(source_frame=source)),))
        result = SimpleNamespace(request=request)
        acknowledgements = []
        records = SimpleNamespace(accepted=acknowledgements.append, projection_events=Counter())
        scene = SimpleNamespace(displayed_frame=source, _projection_current=lambda _: False)
        wrapped = SWEEP.accepted_projection(records, lambda *args: "done")
        self.assertEqual(wrapped(scene, result), "done")
        self.assertEqual(acknowledgements, [])
        self.assertEqual(records.projection_events["stale_same_source_rejected"], 1)
        scene._projection_current = lambda _: True
        self.assertEqual(wrapped(scene, result), "done")
        self.assertEqual(acknowledgements, [request])

    def test_exact_accepted_request_keeps_sweep_stages_across_new_same_source_conversion(self):
        records = SHARED.StageRecords(source_stages=SWEEP.STAGES[:7])
        identity = (1, "partial", 1)
        for when, name in enumerate(SWEEP.STAGES[:7]):
            records.mark(identity, name, when)
        request = SimpleNamespace(traces=(("current", SimpleNamespace(source_frame=object())),),
                                  generation=1, viewport=(0., 1., 100))
        with patch.object(SHARED, "key", return_value=identity), \
             patch.object(SHARED, "perf_counter", side_effect=(7, 70, 8, 9, 10)):
            records.request(request, "projection_offer")
            SWEEP.mark_selected(records, identity)
            records.request(request, "projection_begin")
            records.request(request, "projection_end")
            records.accepted(request)
        records.visibility(True, 10.9)
        SWEEP.paired_paint(records, identity, 11)
        row = records.rows[0]
        self.assertEqual(row["total"], 11000)
        self.assertEqual(row["selected"], 1000)
        self.assertEqual(row["delivery_end"], 1000)
        self.assertAlmostEqual(row["since_show_ms"], 100)
        self.assertEqual(row["geometry"], (1, (0., 1., 100)))
        self.assertEqual(records.frames[identity]["selected"], 70)
        self.assertEqual((records.missing, records.reordered), (0, 0))

    def test_missing_accepted_request_cannot_borrow_complete_frame_history(self):
        records = SHARED.StageRecords()
        identity = (1, "partial", 1)
        for when, name in enumerate(SWEEP.STAGES):
            records.mark(identity, name, when)
        SWEEP.paired_paint(records, identity, 20)
        self.assertFalse(records.rows)
        self.assertEqual(records.missing, 1)
        self.assertEqual(set(records.missing_stages), set(SWEEP.STAGES))

    def test_cli_retains_real_paired_sweep_stages(self):
        with TemporaryDirectory(prefix="app05-sweep-stages-") as folder:
            output = Path(folder) / "result.json"
            result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/profile_app05_sweep_tail.py"),
                "--checkout", str(ROOT), "--output", str(output), "--seconds", "1", "--bins", "65536", "65536"],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            report = json.loads(output.with_suffix(".stages.json").read_text(encoding="utf-8"))
        self.assertGreater(report["retained"], 5)
        self.assertEqual(report["cycles"], 2)
        self.assertTrue(all(report["retained_by_cycle"][str(index)] > 5 for index in (1, 2)))
        self.assertEqual(report["reordered"], 0)
        self.assertIn("steady_visible", report["groups"])
        for row in report["slowest"]:
            self.assertAlmostEqual(sum(row[name] for name in SWEEP.STAGES[1:] + ("paint",)), row["total"])


if __name__ == "__main__":
    unittest.main()
