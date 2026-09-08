"""Regression gate for reproducible synthetic UI2-11J screenshot evidence."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.ui_v2.render_ui2_11j_product_screenshots import render_matrix


class Ui211JScreenshotMatrixTests(unittest.TestCase):
    """Synthetic/offscreen output only; never a visible or hardware acceptance test."""

    def test_all_inert_v2_pages_render_in_the_required_theme_and_size_matrix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ui2-11j-test-") as temporary_directory:
            evidence_directory = Path(temporary_directory)
            manifest = render_matrix(evidence_directory)
            self.assertEqual(manifest["evidence_kind"], "synthetic_offscreen_ui_only")
            self.assertEqual(manifest["capture_count"], 126)
            self.assertEqual(manifest["workspace_ids"], ["home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa"])
            self.assertEqual(manifest["locales"], ["ru", "en"])
            self.assertEqual(manifest["themes"], ["dark", "light", "high_contrast"])
            self.assertEqual(manifest["logical_sizes"], [[1280, 720], [1366, 768], [1920, 1080]])
            captures = manifest["captures"]
            self.assertIsInstance(captures, list)
            self.assertEqual(len(captures), 126)
            observed_cells: set[tuple[str, str, tuple[int, int], str]] = set()
            for capture in captures:
                self.assertIsInstance(capture, dict)
                target = evidence_directory / str(capture["file"])
                self.assertTrue(target.is_file(), target.name)
                self.assertGreater(target.stat().st_size, 1024, target.name)
                observed_cells.add(
                    (
                        str(capture["locale"]),
                        str(capture["theme"]),
                        tuple(capture["logical_size"]),
                        str(capture["workspace"]),
                    )
                )
                self.assertEqual(
                    capture["inspector_hidden_for_narrow_width"],
                    capture["logical_size"][0] < 1600,
                    target.name,
                )
            self.assertEqual(len(observed_cells), 126)
            self.assertTrue((evidence_directory / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
