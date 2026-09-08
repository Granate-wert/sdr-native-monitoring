"""Short automated proof that the UI2-11E synthetic soak runner is executable."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.ui_v2.run_ui2_11e_soak import run_soak


class Ui211ESoakRunnerTests(unittest.TestCase):
    def test_short_soak_keeps_navigation_inert_and_renders(self) -> None:
        # A 150 ms window flakes after a full Qt suite on a busy Windows GUI
        # thread.  This remains a short synthetic smoke test, not a timing
        # benchmark or product-FPS assertion.
        result = run_soak(duration_seconds=0.5, navigation_hz=50.0, render_hz=20.0)
        self.assertGreaterEqual(result.navigation_cycles, 2)
        self.assertGreaterEqual(result.rendered_frames, 1)
        self.assertGreaterEqual(result.maximum_loop_gap_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
