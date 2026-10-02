"""RTL bridge staging is explicit and does not imply vendor runtime or RX."""

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
    validate_manifest,
    validate_rtl_factory,
)

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("RtlExternalFile", "RtlExternalRuntime", "RtlSessionRoute",
           "rtl_enumerate_candidates", "rtl_observe_single_candidate",
           "create_rtl_runtime_control", "rtl_process_is_quarantined")


class RtlBuildContractTests(unittest.TestCase):
    def test_enabled_surface_validation_never_loads_sdk_or_probes(self):
        methods = {name: Mock(side_effect=AssertionError("No SDK calls in build preflight"))
                   for name in METHODS}
        module = SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=True,
                                 RTLSDR_RX_CONTROL_CONTRACT_VERSION=1, **methods)
        manifest = {"rtl_official_compiled": True, "rtl_control_contract_version": 1}
        validate_rtl_factory(module, manifest)
        for method in methods.values():
            method.assert_not_called()
        for name in METHODS:
            with self.subTest(missing=name), self.assertRaisesRegex(ContractSurfaceError, "incomplete"):
                validate_rtl_factory(SimpleNamespace(**{key: value for key, value in vars(module).items()
                                                        if key != name}), manifest)
        for version in (None, True, "1", 0, 2):
            with self.subTest(version=version), self.assertRaises(ContractSurfaceError):
                validate_rtl_factory(module, {**manifest, "rtl_control_contract_version": version})
            with self.subTest(native_version=version), self.assertRaises(ContractSurfaceError):
                validate_rtl_factory(SimpleNamespace(**{**vars(module), "RTLSDR_RX_CONTROL_CONTRACT_VERSION": version}), manifest)

    def test_off_and_legacy_flags_fail_closed_without_new_surface(self):
        validate_rtl_factory(SimpleNamespace(), {})
        validate_rtl_factory(SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=False), {"rtl_official_compiled": False})
        for module, manifest in (
            (SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=True), {}),
            (SimpleNamespace(), {"rtl_official_compiled": True}),
            (SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=0), {"rtl_official_compiled": False}),
            (SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=False), {"rtl_official_compiled": 0}),
            (SimpleNamespace(create_rtl_runtime_control=Mock()), {}),
            (SimpleNamespace(RTLSDR_RX_CONTROL_CONTRACT_VERSION=1), {}),
        ):
            with self.subTest(module=module, manifest=manifest), self.assertRaises(ContractSurfaceError):
                validate_rtl_factory(module, manifest)

    def test_manifest_requires_exact_boolean_and_enabled_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "_sdr_native.cp313-win_amd64.pyd"
            module.write_bytes(b"mock bytes, never imported")
            base = {"cuda_compiled": False, "python_abi": "cp313-win_amd64",
                    "artifact_sha256": _file_sha256(module)}
            for patch in ({}, {"rtl_official_compiled": False},
                          {"rtl_official_compiled": True, "rtl_control_contract_version": 1}):
                validate_manifest(module, {**base, **patch}, expected_cuda=False)
            for patch in ({"rtl_official_compiled": 1}, {"rtl_official_compiled": "false"},
                          {"rtl_official_compiled": True},
                          {"rtl_official_compiled": True, "rtl_control_contract_version": True},
                          {"rtl_official_compiled": False, "rtl_control_contract_version": 1}):
                with self.subTest(patch=patch), self.assertRaises(ContractSurfaceError):
                    validate_manifest(module, {**base, **patch}, expected_cuda=False)

    def test_presets_are_separate_and_cache_option_is_explicit(self):
        presets = json.loads((ROOT / "native/sdr_core/CMakePresets.json").read_text())
        configs = {item["name"]: item for item in presets["configurePresets"]}
        self.assertEqual(configs["base-cpu"]["cacheVariables"]["SDR_CORE_ENABLE_RTLSDR_OFFICIAL"], "OFF")
        for name in ("windows-msvc-cpu-rtl", "windows-msvc-cpu-hackrf-rtl"):
            candidate = configs[name]
            self.assertEqual(candidate["cacheVariables"]["SDR_CORE_ENABLE_RTLSDR_OFFICIAL"], "ON")
            self.assertNotEqual(candidate["binaryDir"], configs[candidate["inherits"]]["binaryDir"])
            self.assertIn(name + "-release", {item["name"] for item in presets["buildPresets"]})
            self.assertIn(name, {item["name"] for item in presets["testPresets"]})
        native = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        self.assertIn('$rtlOfficialOption = if ($EnableRtlOfficial) { "ON" } else { "OFF" }', native)
        self.assertIn('"-DSDR_CORE_ENABLE_RTLSDR_OFFICIAL=$rtlOfficialOption"', native)
        self.assertLess(native.index('if ($EnableRtlOfficial -and'), native.index('$repoRoot ='))

    @unittest.skipUnless(os.name == "nt", "PowerShell guards are Windows-only")
    def test_bad_native_and_release_options_refuse_before_tools(self):
        cases = (
            ("build_native_sdr.ps1", ["-EnableRtlOfficial"], "requires StageOnly CPU Release"),
            ("build_native_sdr.ps1", ["-EnableRtlOfficial", "-StageOnly", "-Lane", "CUDA"], "requires StageOnly CPU Release"),
            ("build_native_sdr.ps1", ["-EnableRtlOfficial", "-StageOnly", "-Profile"], "requires StageOnly CPU Release"),
            ("build_sdr_release.ps1", ["-EnableRtlOfficial"], "requires a tagged full CPU pipeline"),
            ("build_sdr_release.ps1", ["-EnableRtlOfficial", "-OutputTag", "TEST", "-SkipTests"], "requires a tagged full CPU pipeline"),
        )
        for script, arguments, reason in cases:
            with self.subTest(script=script, arguments=arguments):
                result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(ROOT / script), *arguments],
                                        capture_output=True, text=True, timeout=15, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)


if __name__ == "__main__":
    unittest.main()
