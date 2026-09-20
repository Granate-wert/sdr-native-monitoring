"""Normal Direct/Visual observers exercise the public mode setter and same owner."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PersistenceDeliveryTests(unittest.TestCase):
    def test_modes_use_real_ui_setter_without_stage_profiler_or_backend_restart(self):
        with TemporaryDirectory(prefix="app05-density-delivery-") as directory:
            for mode in ("direct", "visual"):
                with self.subTest(mode=mode):
                    output = Path(directory) / (mode + ".json")
                    result = subprocess.run([sys.executable, "-I", "-X", "faulthandler",
                        str(ROOT / "scripts/benchmark_app05_persistence_delivery.py"),
                        "--checkout", str(ROOT), "--output", str(output), "--seconds", "1", "--cycles", "2",
                        "--bins", "4096", "--page-seconds", ".3", "--viewport-seconds", ".2",
                        "--driver-stop-ms", "100", "--persistence-power-bins", "32",
                        "--persistence-render-mode", mode],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    report = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(report["persistence_delivery"]["constructed_modes"], {mode: 1})
                    self.assertFalse(report["persistence_delivery"]["stage_instrumentation"])
                    self.assertNotIn("persistence_profile", report)
                    self.assertNotIn("stage_profile", report)
                    self.assertEqual(len(report["controls"]), 2)
                    self.assertGreater(report["persistence"]["accepted"], 0)
                    self.assertGreater(report["persistence"]["overlay_metrics"]["image_uploads"], 0)
                    self.assertGreater(report["persistence"]["overlay_metrics"]["hidden_updates"], 0)
                    self.assertEqual(report["post_close_allocation_budget"]["reserved_bytes"], 0)
                    self.assertEqual(report["remaining_workers"], [])
                    self.assertEqual(report["product_imports_outside_checkout"], [])


if __name__ == "__main__":
    unittest.main()
