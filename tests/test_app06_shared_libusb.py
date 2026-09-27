"""No DLL loading: shared runtime payloads must have one exact libusb identity."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.preflight_sdr_native_build import _file_sha256
from scripts.preflight_sdr_shared_runtime import validate_shared_runtime
from sdr_monitor.libiio_runtime import LIBIIO_RUNTIME_COMPONENTS

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("hackrf.dll", "libusb-1.0.dll", "pthreadVC3.dll")


class SharedRuntimeTests(unittest.TestCase):
    def fixture(self, directory):
        directory = Path(directory)
        module = directory / "_sdr_native.cp313-win_amd64.pyd"
        module.write_bytes(b"never imported fixture")
        for name in RUNTIMES:
            (directory / name).write_bytes(name.encode())
        manifest = {
            "cuda_compiled": False,
            "python_abi": "cp313-win_amd64",
            "artifact_sha256": _file_sha256(module),
            "hackrf_official_compiled": True,
            "hackrf_factory_contract_version": 2,
            "hackrf_runtime_sha256": {name: _file_sha256(directory / name) for name in RUNTIMES},
            "hackrf_header_sha256": "a" * 64,
            "hackrf_library_sha256": "b" * 64,
        }
        chosen = directory / "chosen-libiio"
        chosen.mkdir()
        for name in LIBIIO_RUNTIME_COMPONENTS:
            (chosen / name).write_bytes(name.encode())
        (chosen / "libusb-1.0.dll").write_bytes((directory / "libusb-1.0.dll").read_bytes())
        return module, manifest, chosen

    def test_exact_bundle_accepts_without_loading_a_native_or_sdk_dll(self):
        with tempfile.TemporaryDirectory() as directory:
            module, manifest, chosen = self.fixture(directory)
            report = validate_shared_runtime(module, manifest, chosen, expected_cuda=False)
            self.assertTrue(report["passed"])
            self.assertEqual(report["selected_libusb_sha256"], report["hackrf_libusb_sha256"])
            self.assertIn("Static", report["scope"])

    def test_different_same_basename_rejected_without_overwriting_either_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            module, manifest, chosen = self.fixture(directory)
            selected = chosen / "libusb-1.0.dll"
            selected.write_bytes(b"different SDK, same filename")
            with self.assertRaisesRegex(ValueError, "shared libusb collision"):
                validate_shared_runtime(module, manifest, chosen, expected_cuda=False)
            self.assertEqual(selected.read_bytes(), b"different SDK, same filename")
            self.assertEqual((module.parent / selected.name).read_bytes(), selected.name.encode())

    def test_missing_selected_and_tampered_admitted_runtime_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            module, manifest, chosen = self.fixture(directory)
            (chosen / "libusb-1.0.dll").unlink()
            with self.assertRaisesRegex(ValueError, "closure missing: libusb"):
                validate_shared_runtime(module, manifest, chosen, expected_cuda=False)
            (module.parent / "hackrf.dll").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "runtime identity mismatch"):
                validate_shared_runtime(module, manifest, chosen, expected_cuda=False)

    def test_baseline_cpu_no_hackrf_still_accepts_its_selected_libusb(self):
        with tempfile.TemporaryDirectory() as directory:
            module, manifest, chosen = self.fixture(directory)
            manifest = {key: value for key, value in manifest.items() if not key.startswith("hackrf_")}
            report = validate_shared_runtime(module, manifest, chosen, expected_cuda=False)
            self.assertFalse(report["official_hackrf"])
            with self.assertRaisesRegex(ValueError, "cuda_compiled"):
                validate_shared_runtime(module, manifest, chosen, expected_cuda=True)

    def test_cli_writes_failed_diagnostic_then_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            module, manifest, chosen = self.fixture(directory)
            manifest_path, output = Path(directory) / "manifest.json", Path(directory) / "result.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (chosen / "libusb-1.0.dll").write_bytes(b"wrong")
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(ROOT / "scripts/preflight_sdr_shared_runtime.py"),
                    "--module",
                    str(module),
                    "--manifest",
                    str(manifest_path),
                    "--libiio-directory",
                    str(chosen),
                    "--lane",
                    "CPU",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            report = json.loads(output.read_text())
            self.assertFalse(report["passed"])
            self.assertIn("collision", report["failure"])

    def test_release_gate_precedes_freezer_and_never_changes_security(self):
        source = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        self.assertLess(source.index("preflight_sdr_shared_runtime.py"), source.index("-m PyInstaller"))
        for forbidden in ("New-NetFirewallRule", "Set-NetFirewallProfile", "netsh"):
            self.assertNotIn(forbidden, source)
