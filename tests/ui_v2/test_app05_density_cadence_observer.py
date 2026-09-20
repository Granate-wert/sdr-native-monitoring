"""Real worker cadence events keep exact request identities and timer state."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DensityCadenceObserverTests(unittest.TestCase):
    def test_actual_worker_events_are_pairable_without_borrowing_cancelled_attempts(self):
        with TemporaryDirectory(prefix="app05-density-cadence-") as directory:
            output = Path(directory) / "cadence.json"
            run = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/profile_app05_rtbw_stages.py"),
                "--checkout", str(ROOT), "--output", str(output), "--seconds", "1", "--cycles", "2",
                "--bins", "4096", "--source-hz", "200", "--driver-stop-ms", "100",
                "--persistence-power-bins", "32", "--persistence-every", "10",
                "--page-seconds", "0", "--viewport-seconds", "0"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        profile = report["stage_profile"]
        self.assertEqual(profile["density_event_evictions"], 0)
        self.assertEqual(profile["density_timer_evictions"], 0)
        self.assertEqual(profile["density_render_evictions"], 0)
        self.assertEqual(profile["gui_interval_evictions"], 0)
        for name in ("graphics_paint:SpectrumScene", "graphics_paint:WaterfallPane", "density_image_paint"):
            self.assertTrue(any(row["stage"] == name for row in profile["gui_intervals"]), name)
        self.assertTrue(any(row["stage"] == "coherent_delivery" for row in profile["gui_intervals"]))
        groups = defaultdict(list)
        for event in profile["density_events"]:
            identity = event["request"]
            self.assertIsInstance(identity[0], int)
            self.assertIsNotNone(identity[1][0])  # exact weak producer binding
            self.assertEqual(identity[1][1], "direct")
            self.assertIsInstance(event["ns"], int)
            groups[json.dumps(identity)].append(event)
        complete = []
        names = ("created", "dispatch", "begin", "end", "uploaded")
        for events in groups.values():
            counts = Counter(event["event"] for event in events)
            if len(events) == 5 and all(counts[name] == 1 for name in names):
                stamps = {event["event"]: event["ns"] for event in events}
                # Appending created may follow synchronous dispatch. The
                # captured time, not deque order, defines causal ordering.
                self.assertEqual([stamps[name] for name in names], sorted(stamps.values()))
                complete.append(events)
        self.assertGreater(len(complete), 0)
        self.assertGreater(len(profile["density_renders"]), 0)
        for render in profile["density_renders"]:
            self.assertLessEqual(render["begin_ns"], render["end_ns"])
            matching = groups[json.dumps(render["request"])]
            uploads = [event for event in matching if event["event"] == "uploaded"]
            self.assertEqual(len(uploads), 1)
            self.assertLessEqual(uploads[0]["ns"], render["begin_ns"])
        timers = profile["density_timer_events"]
        self.assertTrue(any(e["event"] == "flush" and e["pending"] for e in timers))
        self.assertTrue(any(e["event"] == "schedule_after" and e["timer_active"] for e in timers))
        for event in timers:
            if event["pending"]:
                self.assertIsNotNone(event["pending_source"])
            self.assertIn(event["timer_type"], ("CoarseTimer", "PreciseTimer"))
        for name in ("witness_misses", "witness_evictions", "changed_during_paint"):
            self.assertEqual(report[name], 0)
        self.assertEqual(report["post_close_allocation_budget"]["reserved_bytes"], 0)
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["product_imports_outside_checkout"], [])


if __name__ == "__main__":
    unittest.main()
