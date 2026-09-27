"""Official SDK staging stays isolated; presence must not masquerade as RX proof."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.preflight_sdr_native_build import (
    ContractSurfaceError,
    _file_sha256,
    validate_hackrf_factory,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("hackrf.dll", "libusb-1.0.dll", "pthreadVC3.dll")


class OfficialHackrfStagingTests(unittest.TestCase):
    def test_separate_presets_and_fail_closed_baseline(self):
        presets = json.loads((ROOT / "native/sdr_core/CMakePresets.json").read_text())
        configs = {item["name"]: item for item in presets["configurePresets"]}
        base = configs["base-cpu"]
        candidate = configs["windows-msvc-cpu-hackrf"]
        self.assertEqual(base["cacheVariables"]["SDR_CORE_ENABLE_HACKRF_OFFICIAL"], "OFF")
        self.assertNotEqual(candidate["binaryDir"], configs["windows-msvc-cpu"]["binaryDir"])
        self.assertEqual(candidate["cacheVariables"]["SDR_CORE_ENABLE_HACKRF_OFFICIAL"], "ON")
        self.assertIn("$env{SDR_HACKRF", candidate["cacheVariables"]["SDR_CORE_HACKRF_LIBRARY"])
        script = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        self.assertLess(script.index("if ($hackrfRequested)"), script.index("$repoRoot ="))
        self.assertIn('-not $StageOnly -or $Lane -ne "CPU"', script)
        for name in RUNTIMES:
            self.assertIn(name, script)

    @unittest.skipUnless(os.name == "nt", "PowerShell guards are Windows-only")
    def test_bad_sdk_requests_fail_before_compiler_or_activation(self):
        requests = (
            (["-HackrfLibrary", "missing.lib"], "requires StageOnly CPU Release"),
            (["-StageOnly", "-HackrfLibrary", "missing.lib"], "Both Hackrf"),
            (["-StageOnly", "-Lane", "CUDA", "-HackrfLibrary", "missing.lib"], "requires StageOnly CPU Release"),
            (
                ["-StageOnly", "-HackrfLibrary", "missing.lib", "-HackrfIncludeDirectory", "missing"],
                "header/import library is missing",
            ),
        )
        for arguments, reason in requests:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-File",
                        str(ROOT / "build_native_sdr.ps1"),
                        *arguments,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)

    def test_factory_presence_never_calls_it_or_probes_a_radio(self):
        factory = Mock()
        validate_hackrf_factory(
            SimpleNamespace(create_hackrf_runtime_dsp_control=factory, HACKRF_FACTORY_CONTRACT_VERSION=2),
            {"hackrf_official_compiled": True, "hackrf_factory_contract_version": 2},
        )
        validate_hackrf_factory(SimpleNamespace(), {})
        factory.assert_not_called()
        for version in (None, 1, True, "2"):
            with self.subTest(version=version), self.assertRaisesRegex(ContractSurfaceError, "contract version"):
                validate_hackrf_factory(
                    SimpleNamespace(create_hackrf_runtime_dsp_control=factory, HACKRF_FACTORY_CONTRACT_VERSION=version),
                    {"hackrf_official_compiled": True, "hackrf_factory_contract_version": 2},
                )
        for module, manifest in (
            (SimpleNamespace(), {"hackrf_official_compiled": True}),
            (SimpleNamespace(create_hackrf_runtime_dsp_control=factory), {}),
        ):
            with self.assertRaisesRegex(ContractSurfaceError, "factory does not match"):
                validate_hackrf_factory(module, manifest)

    def test_runtime_manifest_rejects_missing_tampered_extra_and_invalid_sdk_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            module = directory / "_sdr_native.cp313-win_amd64.pyd"
            module.write_bytes(b"test module only, never imported")
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
            validate_manifest(module, manifest, expected_cuda=False)
            for name in RUNTIMES:
                path = directory / name
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ContractSurfaceError, "runtime identity mismatch"):
                    validate_manifest(module, manifest, expected_cuda=False)
                path.write_bytes(b"wrong bundle")
                with self.assertRaisesRegex(ContractSurfaceError, "runtime identity mismatch"):
                    validate_manifest(module, manifest, expected_cuda=False)
                path.write_bytes(original)
            for update in (
                {"hackrf_official_compiled": "true"},
                {"hackrf_factory_contract_version": True},
                {"hackrf_header_sha256": "unknown"},
                {"hackrf_library_sha256": None},
                {"hackrf_runtime_sha256": {**manifest["hackrf_runtime_sha256"], "extra.dll": "a" * 64}},
            ):
                with self.subTest(update=update), self.assertRaises(ContractSurfaceError):
                    validate_manifest(module, {**manifest, **update}, expected_cuda=False)
