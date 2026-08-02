"""P16UI-10 release/package acceptance smoke tests."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from esw_dfl.ui.identity import CURRENT_IDENTITY


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class ProductIdentityTests(unittest.TestCase):
    def test_identity_has_version_and_display_name(self) -> None:
        self.assertRegex(CURRENT_IDENTITY.version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(CURRENT_IDENTITY.display_name, "SDR Native Monitoring")
        self.assertEqual(CURRENT_IDENTITY.organization_name, "SDRNativeMonitoring")
        self.assertEqual(CURRENT_IDENTITY.application_name, "SDR Native Monitoring")
        self.assertEqual(CURRENT_IDENTITY.executable_name, "sdr_native_monitoring")


class PyProjectTests(unittest.TestCase):
    def test_pyproject_declares_project_and_license(self) -> None:
        with open("pyproject.toml", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertIn("[build-system]", raw)
        self.assertIn('name = "sdr-native-monitoring"', raw)
        self.assertIn('version = "0.16.10"', raw)
        self.assertIn('requires-python = ">=3.13"', raw)

    def test_root_license_exists(self) -> None:
        with open("LICENSE", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("Copyright", content)
        self.assertIn("2026", content)


class CMakePresetTests(unittest.TestCase):
    def test_jetson_cuda_preset_is_scaffolded(self) -> None:
        import json

        with open("native/sdr_core/CMakePresets.json", encoding="utf-8") as handle:
            presets = json.load(handle)
        names = {preset["name"] for preset in presets["configurePresets"]}
        self.assertIn("linux-aarch64-cuda", names)
        jetson = next(p for p in presets["configurePresets"] if p["name"] == "linux-aarch64-cuda")
        self.assertEqual(jetson["cacheVariables"]["CMAKE_CUDA_ARCHITECTURES"], "87")
        self.assertEqual(jetson["cacheVariables"]["SDR_CORE_ENABLE_CUDA"], "ON")
        self.assertEqual(jetson["cacheVariables"]["SDR_CORE_BUILD_PYTHON"], "OFF")


class NativeModulesTests(unittest.TestCase):
    def test_sdr_native_cp313_artifact_present(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        candidates = list(root.glob("esw_dfl/_sdr_native.cp313-win_amd64.pyd"))
        self.assertTrue(candidates, "current-ABI _sdr_native .pyd must exist for packaging")

    def test_sgram_native_source_exists(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        cargo = root / "native" / "sgram_decoder" / "Cargo.toml"
        lib = root / "native" / "sgram_decoder" / "src" / "lib.rs"
        self.assertTrue(cargo.is_file())
        self.assertTrue(lib.is_file())
        content = lib.read_text(encoding="utf-8")
        self.assertIn("decode_sgram_line", content)


class P15AcceptanceSmokeTests(unittest.TestCase):
    def test_p15_offline_validation_runs_and_writes_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    "benchmark_p15_validation.py",
                    "--benchmark-repeats",
                    "1",
                    "--recording-blocks",
                    "4",
                    "--output-dir",
                    tmp,
                ],
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report_path = os.path.join(tmp, "p15_validation.json")
            self.assertTrue(os.path.isfile(report_path))


if __name__ == "__main__":
    unittest.main()
