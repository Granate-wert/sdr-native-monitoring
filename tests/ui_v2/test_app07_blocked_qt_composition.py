"""Actual blocked Qt V2 callback with ORIGINAL product ownership and MOCK RX."""

import json
from pathlib import Path
import subprocess
import sys
import unittest


class BlockedQtCompositionTests(unittest.TestCase):
    def test_two_producers_continue_while_qt_blocks_and_scoped_stop_retires_only_target(self):
        root = Path(__file__).resolve().parents[2]
        child = subprocess.run([sys.executable, str(root / "tests/qt_blocked_two_source_probe.py")],
                               cwd=root, capture_output=True, text=True, timeout=45, check=False)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn('"phase": "qt_blocked_two_producer_delivery"', child.stderr)
        self.assertIn("Timeout", child.stderr)
        self.assertIn("in blocked_callback", child.stderr)
        self.assertNotIn("Traceback (most recent call last):", child.stderr)
        rows = [line.removeprefix("G04_ORIGINAL_COMPOSITION ") for line in child.stdout.splitlines()
                if line.startswith("G04_ORIGINAL_COMPOSITION ")]
        self.assertEqual(len(rows), 1)
        result = json.loads(rows[0])
        self.assertTrue(result["cleanup_confirmed"] and result["selected_stop_peer_continued"])
        self.assertTrue(result["watchdog_armed_inside_callback"])
        self.assertTrue(result["watchdog_closed_before_callback_return"])
        self.assertLessEqual(result["pending_max"], 2)
        self.assertTrue(all(delta >= 3 for delta in result["producer_deltas"] + result["prepared_deltas"]))
        self.assertGreater(result["superseded_delta"], 0)


if __name__ == "__main__":
    unittest.main()
