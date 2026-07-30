from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.display import WorkArea, calculate_window_geometry


class DisplayGeometryTests(unittest.TestCase):
    def test_100_percent_keeps_base_size_on_full_hd(self) -> None:
        result = calculate_window_geometry(WorkArea(0, 0, 1920, 1040), 96)
        self.assertEqual((result.width, result.height), (1380, 850))
        self.assertGreaterEqual(result.x, 0)
        self.assertGreaterEqual(result.y, 0)

    def test_200_percent_scales_logically_on_4k(self) -> None:
        result = calculate_window_geometry(WorkArea(0, 0, 3840, 2080), 192)
        self.assertEqual((result.width, result.height), (2760, 1700))
        self.assertEqual((result.width // 2, result.height // 2), (1380, 850))

    def test_high_dpi_never_exceeds_small_work_area(self) -> None:
        work = WorkArea(10, 20, 1366, 728)
        result = calculate_window_geometry(work, 288)
        self.assertLessEqual(result.width, int(work.width * 0.94))
        self.assertLessEqual(result.height, int(work.height * 0.92))
        self.assertLessEqual(result.min_width, result.width)
        self.assertLessEqual(result.min_height, result.height)
        self.assertGreaterEqual(result.x, work.left)
        self.assertGreaterEqual(result.y, work.top)

    def test_tiny_resolution_still_fits(self) -> None:
        work = WorkArea(0, 0, 420, 300)
        result = calculate_window_geometry(work, 384)
        self.assertLessEqual(result.width, work.width)
        self.assertLessEqual(result.height, work.height)
        self.assertLessEqual(result.min_width, result.width)
        self.assertLessEqual(result.min_height, result.height)


if __name__ == "__main__":
    unittest.main()
