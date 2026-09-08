"""UI2-11I logical-DPI regression across clean offscreen Qt processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).parents[2]
_PROBE = _ROOT / "tests" / "ui_v2" / "ui2_11i_logical_dpi_probe.py"


class Ui211ILogicalDpiMatrixTests(unittest.TestCase):
    """Logical Qt-scale geometry guard; never substitutes for visible Windows DPI review."""

    def test_all_admitted_pages_remain_semantic_and_unclipped_at_required_logical_sizes(self) -> None:
        for width, height in ((1280, 720), (1366, 768)):
            for scale in ("1", "1.5", "2", "3"):
                with self.subTest(width=width, height=height, scale=scale):
                    payload = _run_probe(scale, width=width, height=height)
                    self.assertEqual(payload["scale"], scale)
                    self.assertEqual(payload["requested_size"], [width, height])
                    self.assertGreater(payload["pixmap_width"], 0)
                    self.assertGreater(payload["pixmap_height"], 0)
                    self.assertEqual(
                        [page["workspace"] for page in payload["pages"]],
                        ["home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa"],
                    )
                    self.assertEqual(payload["inspector_hidden_for_narrow_width"], width < 1600)
                    for page in payload["pages"]:
                        self.assertTrue(page["primary_visible"], page["workspace"])
                        self.assertTrue(page["primary_enabled"], page["workspace"])
                        self.assertTrue(page["primary_named"], page["workspace"])
                        self.assertTrue(page["primary_within_shell"], page["workspace"])
                        self.assertTrue(page["page_minimum_fits"], page["workspace"])
                        self.assertEqual(page["missing_semantic_names"], [], page["workspace"])


def _run_probe(scale: str, *, width: int, height: int) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = scale
    completed = subprocess.run(
        [sys.executable, str(_PROBE), "--scale", scale, "--width", str(width), "--height", str(height)],
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    if completed.returncode != 0:
        raise AssertionError(f"scale={scale} probe failed:\nstdout={completed.stdout}\nstderr={completed.stderr}")
    return json.loads(completed.stdout)


if __name__ == "__main__":
    unittest.main()
