"""No-device probe validation and exact counter-delta accounting."""

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.probe_app06_hackrf_native_maxfs import counter_delta

ROOT = Path(__file__).resolve().parents[1]


class NativeMaxFsProbeTests(unittest.TestCase):
    def test_counter_delta_rejects_invalid_or_regressed_not_zero(self):
        self.assertEqual(counter_delta(SimpleNamespace(count=4), SimpleNamespace(count=10), "count"), 6)
        for first, last in ((10, 4), (False, 4), (4, True), (4, 4.0)):
            with self.subTest(first=first, last=last), self.assertRaises(ValueError):
                counter_delta(SimpleNamespace(count=first), SimpleNamespace(count=last), "count")

    def test_rx_switch_is_required_before_importing_native_or_sdk(self):
        result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/probe_app06_hackrf_native_maxfs.py"),
            "--module", "missing.pyd", "--manifest", "missing.json", "--output", "must-not-create.json"],
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--rx and seconds5..120 required", result.stderr)
        self.assertFalse((ROOT / "must-not-create.json").exists())
