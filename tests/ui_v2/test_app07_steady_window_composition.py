"""Original V2 keeps delivering while bounded cached observation runs off Qt."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class SteadyWindowCompositionTests(unittest.TestCase):
    def test_cached_observer_does_not_block_qt_delivery_and_joins_before_reader_close(self):
        root = Path(__file__).resolve().parents[2]
        child = subprocess.run([sys.executable, str(root / "tests/qt_steady_window_probe.py")],
            cwd=root, capture_output=True, text=True, timeout=45, check=False)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertNotIn("Traceback (most recent call last):", child.stderr)
        rows = [line.removeprefix("M78_STEADY_ORIGINAL ") for line in child.stdout.splitlines()
                if line.startswith("M78_STEADY_ORIGINAL ")]
        self.assertEqual(len(rows), 1)
        report = json.loads(rows[0])
        self.assertTrue(report["cleanup_confirmed"] and report["callback_off_qt"])
        self.assertGreater(report["queue_delivered_delta"], 0)
        self.assertGreaterEqual(report["observations"], 2)
        self.assertLessEqual(report["observations"], 8)


if __name__ == "__main__":
    unittest.main()
