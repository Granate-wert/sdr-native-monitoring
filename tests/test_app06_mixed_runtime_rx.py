"""Reject accidental physical execution before native/SDK loading."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MixedRuntimeAdmissionTests(unittest.TestCase):
    def test_explicit_rx_and_bounded_duration_precede_native_runtime_loading(self):
        for extra in ([], ["--rx", "--duration", "nan"], ["--rx", "--duration", "31"]):
            with self.subTest(extra=extra):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(ROOT / "scripts/probe_app06_mixed_runtime_rx.py"),
                        "--module",
                        "missing.pyd",
                        "--manifest",
                        "missing.json",
                        "--shared-directory",
                        "missing",
                        "--pluto-uri",
                        "usb:missing",
                        "--output",
                        "must-not-exist-mixed.json",
                        *extra,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("--rx and duration5..30 required", result.stderr)
                self.assertFalse((ROOT / "must-not-exist-mixed.json").exists())
