"""R12-F tests for standalone frozen-package native-artifact admission."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.preflight_sdr_release import build_manifest, verify_manifest


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_MODULES = tuple((ROOT / "sdr_monitor").glob("_sdr_native*.pyd"))


def _frozen_package_verifier() -> ModuleType:
    """Load the release helper through its supported direct-script import path."""

    verifier_path = ROOT / "scripts" / "verify_sdr_frozen_package.py"
    spec = importlib.util.spec_from_file_location("r12f_frozen_verifier", verifier_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen package verifier")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(verifier_path.parent), *sys.path]):
        spec.loader.exec_module(module)
    return module


class R12FFrozenPackageAdmissionTests(unittest.TestCase):
    def test_release_manifest_is_stable_and_rejects_an_unrecorded_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / "SDRNativeMonitoring.exe").write_bytes(b"portable executable")
            (package / "_internal").mkdir()
            (package / "_internal/python313.dll").write_bytes(b"abi")
            manifest_path = package / "release_manifest.json"
            manifest_path.write_text(
                json.dumps(build_manifest(package, "CUDA", "0.16.10")),
                encoding="utf-8",
            )

            observed = verify_manifest(package, manifest_path, "CUDA", "0.16.10")
            self.assertEqual(len(observed["files"]), 2)

            (package / "_internal/unrecorded.bin").write_bytes(b"mutation")
            with self.assertRaisesRegex(ValueError, "does not match distribution"):
                verify_manifest(package, manifest_path, "CUDA", "0.16.10")

    @unittest.skipUnless(len(ACTIVE_MODULES) == 1, "active native Release artifact is unavailable")
    def test_source_metadata_command_imports_native_before_ui_or_device_code(self) -> None:
        completed = subprocess.run(
            [sys.executable, "main_sdr.py", "--verify-native-artifact"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        observed = json.loads(completed.stdout)
        self.assertEqual(observed["native_module"], "sdr_monitor._sdr_native")
        self.assertEqual(Path(observed["native_path"]).resolve(), ACTIVE_MODULES[0].resolve())
        self.assertIsInstance(observed["cuda_compiled"], bool)

    def test_release_script_requires_manifest_and_frozen_native_verification(self) -> None:
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")

        self.assertIn("--verify-existing", release)
        self.assertIn("verify_sdr_frozen_package.py", release)
        self.assertGreater(
            release.index("verify_sdr_frozen_package.py"),
            release.index("release manifest verification failed"),
        )

    def test_metadata_native_command_returns_before_gui_import(self) -> None:
        source = (ROOT / "sdr_monitor/main.py").read_text(encoding="utf-8")
        main_start = source.index("def main(")
        gui_import = source.index("from PySide6.QtWidgets import QApplication", main_start)

        self.assertLess(source.index("if arguments.verify_native_artifact:", main_start), gui_import)

    def test_frozen_verifier_rejects_native_lane_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            executable = package / "SDRNativeMonitoring.exe"
            executable.write_bytes(b"portable executable")
            native = package / "_internal" / "sdr_monitor" / "_sdr_native.cp313-win_amd64.pyd"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"native")
            manifest_path = package / "release_manifest.json"
            manifest_path.write_text(
                json.dumps(build_manifest(package, "CPU", "0.16.10")),
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {
                        "native_module": "sdr_monitor._sdr_native",
                        "native_path": str(native),
                        "schema": "sdr-native-contracts",
                        "schema_version": 5,
                        "cuda_compiled": True,
                    }
                ),
                stderr="",
            )
            verifier = _frozen_package_verifier()
            with patch.object(verifier.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(ValueError, "CUDA lane does not match"):
                    verifier.verify_frozen_package(package, manifest_path, "CPU", "0.16.10")


if __name__ == "__main__":
    unittest.main()
