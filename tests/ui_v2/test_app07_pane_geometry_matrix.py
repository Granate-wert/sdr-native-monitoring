"""Inert 3-source+Empty UI V2 layout at FHD/QHD logical DPI sizes.

Each scale runs in a fresh Qt process. This is widget geometry, not Windows
per-monitor DPI, live hardware, raster FPS or 65%-area acceptance.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tests" / "ui_v2" / "app07_pane_geometry_probe.py"


class IndependentPaneGeometryMatrixTests(unittest.TestCase):
    def test_three_distinct_source_panels_and_empty_fourth_remain_reachable(self) -> None:
        cases = (
            ("FHD 100%", 1920, 980, "1", "ru", False),
            ("FHD 150%", 1280, 640, "1.5", "ru", False),
            ("FHD 200%", 960, 460, "2", "ru", True),
            ("FHD 300%", 640, 280, "3", "ru", True),
            ("QHD 100%", 2560, 1340, "1", "en", False),
            ("QHD 150%", 1707, 860, "1.5", "en", False),
            ("QHD 200%", 1280, 620, "2", "en", False),
            ("QHD 300%", 853, 380, "3", "en", True),
        )
        for name, width, height, scale, locale, stacked in cases:
            with self.subTest(name=name):
                completed = subprocess.run(
                    (sys.executable, str(PROBE), "--width", str(width), "--height", str(height),
                     "--scale", scale, "--locale", locale),
                    cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["scroll_actual"], [width, height])
                self.assertEqual(result["viewport"][1], height)
                self.assertEqual(result["stacked"], stacked)
                self.assertEqual(result["empty_slots"], [4])
                self.assertTrue(result["last_slot_reachable"])
                self.assertTrue(result["grab_nonnull"])
                self.assertEqual(result["scroll_max"][0], 0)
                self.assertEqual(len(result["panes"]), 3)
                self.assertTrue(all(item["timing_fits"] and item["cell_inside_board"]
                                    for item in result["panes"]))
                self.assertTrue(all(item["spectrum"][1] >= 140
                                    and item["waterfall"][1] >= 80
                                    for item in result["panes"]))
                if stacked:
                    self.assertGreater(result["scroll_max"][1], 0)
                    self.assertEqual({item["cell_pos"][0] for item in result["panes"]}, {0})
                else:
                    self.assertEqual(result["scroll_max"][1], 0)
                    self.assertGreater(result["panes"][1]["cell_pos"][0], 0)


if __name__ == "__main__":
    unittest.main()
