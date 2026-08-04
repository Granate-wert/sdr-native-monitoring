"""S12 standalone release/preflight contract tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.preflight_sdr_release import build_manifest


ROOT = Path(__file__).resolve().parents[1]


class S12PackagingTests(unittest.TestCase):
    def test_release_preflight_hashes_cpu_and_cuda_package_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / "SDRNativeMonitoring.exe").write_bytes(b"portable executable")
            (package / "python313.dll").write_bytes(b"abi")
            cpu = build_manifest(package, "CPU", "0.16.10")
            cuda = build_manifest(package, "CUDA", "0.16.10")
            self.assertEqual(cpu["python_abi"], "cp313")
            self.assertEqual(cpu["lane"], "CPU")
            self.assertEqual(cuda["lane"], "CUDA")
            self.assertEqual(len(cpu["files"]), 2)
            self.assertTrue(all(len(item["sha256"]) == 64 for item in cpu["files"]))

    def test_preflight_rejects_legacy_or_spectrogram_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / "SDRNativeMonitoring.exe").write_bytes(b"exe")
            (package / "bad.py").write_text("import esw_dfl", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_manifest(package, "CPU", "0.16.10")

    def test_scripts_and_python_policy_are_standalone(self) -> None:
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        native = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("--exclude-module esw_dfl", release)
        self.assertIn("--exclude-module _sgram_native", release)
        self.assertIn("Python 3.13", release)
        self.assertIn("--add-binary", release)
        self.assertIn("sdr_monitor", release)
        self.assertIn('EXT_SUFFIX', native)
        self.assertIn('Join-Path $repoRoot "sdr_monitor"', native)
        self.assertNotIn('Join-Path $repoRoot "esw_dfl"', native)
        self.assertIn('target-version = "py313"', pyproject)
        self.assertIn('python_version = "3.13"', pyproject)


if __name__ == "__main__":
    unittest.main()
