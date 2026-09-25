"""Guard the diagnostic transparent-layer equality fixture, not a product cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "probe_app05_history_raster.py"


class HistoryRasterProbeTests(unittest.TestCase):
    def test_offscreen_qimage_replay_is_exact_for_bounded_dot_styles(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "offscreen"
            completed = subprocess.run(
                (sys.executable, "-I", str(PROBE), "--output", str(output),
                 "--samples", "2"),
                cwd=ROOT, env=environment, capture_output=True, text=True,
                timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["qpa"], "offscreen")
            self.assertEqual(len(report["results"]), 8)
            self.assertEqual(report["scene_results"], [])
            for row in report["results"]:
                with self.subTest(style=row["style"], dpr=row["dpr"],
                                  fixture=row["fixture"]):
                    self.assertEqual(row["pixels_changed"], 0)
                    self.assertEqual(row["channels_changed"], 0)
                    self.assertEqual(row["max_channel_delta"], 0)
                    self.assertLessEqual(row["layer_bytes"], 5 * 1024 * 1024)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("APP05_VISIBLE_DPR"),
                         "opt-in visible Windows Qt test requires APP05_VISIBLE_DPR")
    def test_visible_v2_scene_matrix_has_exact_build_hit_and_control(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "scene.json"
            environment = os.environ.copy()
            environment["QT_QPA_PLATFORM"] = "windows"
            completed = subprocess.run(
                (sys.executable, "-I", str(PROBE), "--output", str(output),
                 "--samples", "2", "--scene"),
                cwd=ROOT, env=environment, capture_output=True, text=True,
                timeout=60, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
            report = json.loads(output.read_text(encoding="utf-8"))
            rows = report["scene_results"]
            self.assertEqual(len(rows), 12)
            self.assertEqual({row["kind"] for row in rows}, {"MAXIMUM", "MINIMUM"})
            for row in rows:
                with self.subTest(kind=row["kind"], state=row["state"]):
                    self.assertAlmostEqual(row["dpr"],
                                           float(os.environ["APP05_VISIBLE_DPR"]))
                    self.assertEqual(row["first_pixels_changed"], 0)
                    self.assertEqual(row["hit_pixels_changed"], 0)
                    self.assertEqual(row["baseline_repeat_pixels_changed"], 0)
                    self.assertEqual((row["builds"], row["hits"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
