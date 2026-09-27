"""Package/load-only closure; no fake result is treated as physical RX proof."""

from __future__ import annotations

import builtins
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.preflight_sdr_release import build_manifest
from scripts.verify_sdr_frozen_shared_runtime import verify_frozen_shared_runtime
from sdr_monitor import frozen_shared_runtime as runtime
from sdr_monitor import main as main_module
from sdr_monitor.libiio_runtime import LIBIIO_RUNTIME_COMPONENTS

ROOT = Path(__file__).resolve().parents[1]


def package_fixture(package: Path) -> tuple[Path, Path, dict]:
    directory = package / "_internal" / "sdr_monitor"
    directory.mkdir(parents=True)
    (package / "SDRNativeMonitoring.exe").write_bytes(b"fake executable, never launched")
    module = directory / "_sdr_native.cp313-win_amd64.pyd"
    module.write_bytes(b"fake module, never imported")
    for name in runtime.SHARED_COMPONENTS:
        (directory / name).write_bytes(name.encode())
    manifest = {
        "cuda_compiled": False, "python_abi": "cp313-win_amd64",
        "artifact_sha256": runtime.file_sha256(module), "source_commit": "a" * 40,
        "hackrf_official_compiled": True, "hackrf_factory_contract_version": 2,
        "hackrf_runtime_sha256": {n: runtime.file_sha256(directory / n) for n in runtime.HACKRF_COMPONENTS},
        "hackrf_header_sha256": "b" * 64, "hackrf_library_sha256": "c" * 64,
    }
    (directory / "native_build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    inputs = {
        "schema": "app06-shared-libusb-preflight-v1", "passed": True, "official_hackrf": True,
        "source_commit": manifest["source_commit"],
        "selected_libusb_sha256": manifest["hackrf_runtime_sha256"]["libusb-1.0.dll"],
        "hackrf_libusb_sha256": manifest["hackrf_runtime_sha256"]["libusb-1.0.dll"],
        "libiio_runtime_sha256": {n: runtime.file_sha256(directory / n) for n in LIBIIO_RUNTIME_COMPONENTS},
    }
    (package / "shared_runtime_inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    release = package / "release_manifest.json"
    release.write_text(json.dumps(build_manifest(package, "CPU", "0.16.10")), encoding="utf-8")
    return module, release, manifest


def native_fixture(module: Path) -> SimpleNamespace:
    return SimpleNamespace(
        __file__=str(module), HACKRF_FACTORY_CONTRACT_VERSION=2,
        create_hackrf_runtime_dsp_control=Mock(),
        build_info=Mock(return_value={"pluto_compiled": True, "cuda_compiled": False}),
        pluto_runtime_info=Mock(return_value=SimpleNamespace(
            available=True, library_path=str(module.parent / "libiio.dll"))),
        scan_pluto_contexts=Mock(), PlutoDevice=Mock(), hackrf_init=Mock(),
    )


class SharedRuntimeDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(runtime, "hold_package_libiio_metadata", return_value=nullcontext()))

    def test_exact_manifest_sdk_and_all_loaded_modules_without_hardware_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, _ = package_fixture(Path(tmp))
            native = native_fixture(module)
            paths = tuple(module.parent / n for n in runtime.SHARED_COMPONENTS)
            with (patch.object(sys, "frozen", True, create=True),
                  patch.object(runtime, "windows_loaded_module_paths", return_value=paths),
                  patch.dict(os.environ, {"LIBIIO_DLL_PATH": "external-must-not-load.dll"})):
                verdict = runtime.packaged_shared_runtime_verdict(native)
                self.assertEqual(os.environ["LIBIIO_DLL_PATH"], str(module.parent / "libiio.dll"))
            self.assertEqual(len(verdict["loaded_shared_modules"]), 9)
            self.assertEqual(len(verdict["runtime_file_sha256"]), 9)
            for call in (native.create_hackrf_runtime_dsp_control, native.scan_pluto_contexts,
                         native.PlutoDevice, native.hackrf_init):
                call.assert_not_called()
            native.pluto_runtime_info.assert_called_once_with()

    def test_source_tree_and_bad_manifest_refuse_before_libiio_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp))
            native = native_fixture(module)
            with patch.object(sys, "frozen", False, create=True), self.assertRaisesRegex(RuntimeError, "frozen"):
                runtime.packaged_shared_runtime_verdict(native)
            for updates in ({"artifact_sha256": "0" * 64}, {"hackrf_official_compiled": False},
                            {"hackrf_factory_contract_version": True}, {"cuda_compiled": True},
                            {"python_abi": "cp312-win_amd64"}, {"source_commit": "unknown"},
                            {"hackrf_header_sha256": None}, {"hackrf_runtime_sha256": {}}):
                with self.subTest(updates=updates), patch.object(sys, "frozen", True, create=True):
                    (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest, **updates}))
                    with self.assertRaises(RuntimeError):
                        runtime.packaged_shared_runtime_verdict(native)
            native.pluto_runtime_info.assert_not_called()

    def test_oversized_manifest_tampered_sdk_missing_factory_and_external_libiio_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp))
            native = native_fixture(module)
            path = module.parent / "native_build_manifest.json"
            with patch.object(sys, "frozen", True, create=True):
                path.write_bytes(b" " * 16_385)
                with self.assertRaisesRegex(RuntimeError, "bound"):
                    runtime.packaged_shared_runtime_verdict(native)
                path.write_text(json.dumps(manifest))
                dll = module.parent / "hackrf.dll"
                dll.write_bytes(b"tampered")
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    runtime.packaged_shared_runtime_verdict(native)
                dll.write_bytes(b"hackrf.dll")
                native.HACKRF_FACTORY_CONTRACT_VERSION = 1
                with self.assertRaisesRegex(RuntimeError, "factory2"):
                    runtime.packaged_shared_runtime_verdict(native)
                native.HACKRF_FACTORY_CONTRACT_VERSION = 2
                native.pluto_runtime_info.return_value.library_path = str(module)
                with self.assertRaisesRegex(RuntimeError, "package-local"):
                    runtime.packaged_shared_runtime_verdict(native)

    def test_inventory_rejects_duplicate_even_equal_bytes_missing_external_and_unbounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, _ = package_fixture(Path(tmp))
            directory = module.parent
            paths = tuple(directory / n for n in runtime.REQUIRED_LOADED)
            self.assertEqual(len(runtime.validate_loaded_shared_runtime(directory, paths)), 4)
            other = Path(tmp) / "LIBUSB-1.0.DLL"
            other.write_bytes(b"libusb-1.0.dll")
            for invalid in ((*paths, other), (*paths, paths[0]), (),
                            tuple(p if p.name != "libusb-1.0.dll" else other for p in paths),
                            (module,) * 4097):
                with self.subTest(paths=len(invalid)), self.assertRaises(ValueError):
                    runtime.validate_loaded_shared_runtime(directory, invalid)

    @unittest.skipUnless(os.name == "nt", "Windows process inventory only")
    def test_actual_windows_inventory_is_bounded_absolute_and_includes_python_runtime(self):
        paths = runtime.windows_loaded_module_paths()
        self.assertLessEqual(len(paths), 4096)
        self.assertTrue(all(p.is_absolute() and p.is_file() for p in paths))
        self.assertTrue(any(p.name.casefold() == "python313.dll" for p in paths))

    @unittest.skipUnless(os.name == "nt", "Windows API failure injection")
    def test_failed_or_unstable_windows_inventory_is_not_silent_success(self):
        kernel = SimpleNamespace(GetCurrentProcess=Mock(return_value=1),
                                 K32EnumProcessModules=Mock(return_value=0), GetModuleFileNameW=Mock())
        with patch.object(runtime.ctypes, "WinDLL", return_value=kernel):
            with self.assertRaisesRegex(RuntimeError, "inventory failed"):
                runtime.windows_loaded_module_paths()
            def grow(_process, _handles, byte_count, needed):
                needed._obj.value = byte_count + runtime.ctypes.sizeof(runtime.ctypes.c_void_p)
                return 1
            kernel.K32EnumProcessModules.side_effect = grow
            with self.assertRaisesRegex(RuntimeError, "stabilize"):
                runtime.windows_loaded_module_paths()
            kernel.GetModuleFileNameW.assert_not_called()

    def test_cli_is_exclusive_and_returns_before_logging_or_gui(self):
        self.assertTrue(main_module._arguments(["--verify-packaged-shared-runtime"]).verify_packaged_shared_runtime)
        with self.assertRaises(SystemExit):
            main_module._arguments(["--verify-packaged-shared-runtime", "--verify-native-artifact"])
        fake = SimpleNamespace()
        with (patch.object(main_module.importlib, "import_module", return_value=fake),
              patch.object(runtime, "packaged_shared_runtime_verdict", return_value={"test": True}) as verdict,
              patch.object(main_module, "_configure_logging") as logger,
              patch.object(builtins, "print") as output):
            self.assertEqual(main_module.main(["--verify-packaged-shared-runtime"]), 0)
            verdict.assert_called_once_with(fake)
            logger.assert_not_called()
            self.assertEqual(json.loads(output.call_args.args[0]), {"test": True})


class FrozenSharedRuntimeVerifierTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(runtime, "hold_package_libiio_metadata", return_value=nullcontext()))

    def test_input_report_mismatch_refuses_even_if_final_release_records_every_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp).resolve()
            _, release, _ = package_fixture(package)
            path = package / "shared_runtime_inputs.json"
            recorded = json.loads(path.read_text())
            for updates in ({"passed": False}, {"official_hackrf": False}, {"source_commit": "unknown"},
                            {"libiio_runtime_sha256": {}}, {"selected_libusb_sha256": "0" * 64}):
                path.write_text(json.dumps({**recorded, **updates}))
                release.write_text(json.dumps(build_manifest(package, "CPU", "0.16.10")))
                with (self.subTest(updates=updates),
                      patch("scripts.verify_sdr_frozen_shared_runtime.subprocess.run") as run,
                      self.assertRaisesRegex(ValueError, "pre-freeze inputs")):
                    verify_frozen_shared_runtime(package, release, "0.16.10")
                run.assert_not_called()

    def test_mock_frozen_contract_and_isolated_path_then_refuse_forged_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp).resolve()
            module, release, _ = package_fixture(package)
            native = native_fixture(module)
            with (patch.object(sys, "frozen", True, create=True),
                  patch.object(runtime, "windows_loaded_module_paths",
                               return_value=tuple(module.parent / n for n in runtime.SHARED_COMPONENTS)),
                  patch.dict(os.environ, {}, clear=False)):
                verdict = runtime.packaged_shared_runtime_verdict(native)
            completed = subprocess.CompletedProcess([], 0, json.dumps(verdict), "")
            with patch("scripts.verify_sdr_frozen_shared_runtime.subprocess.run", return_value=completed) as run:
                self.assertEqual(verify_frozen_shared_runtime(package, release, "0.16.10"), verdict)
                self.assertEqual(run.call_args.args[0][1], "--verify-packaged-shared-runtime")
                self.assertNotIn(str(ROOT), run.call_args.kwargs["env"]["PATH"])
                self.assertIn("external-runtime", run.call_args.kwargs["env"]["LIBIIO_DLL_PATH"])
                for update in ({"sdk_initialized": True}, {"rx_attempted": True}, {"metadata_hold_released": False},
                               {"hackrf_factory_contract_version": True}, {"native_source_commit": "x"},
                               {"native_sha256": "0" * 64}, {"runtime_file_sha256": {}},
                               {"loaded_shared_modules": []}):
                    with self.subTest(update=update), self.assertRaises(ValueError):
                        completed.stdout = json.dumps({**verdict, **update})
                        verify_frozen_shared_runtime(package, release, "0.16.10")
                for mutation in ("duplicate", "nonlocal", "hash"):
                    altered = copy.deepcopy(verdict)
                    if mutation == "duplicate":
                        altered["loaded_shared_modules"][1] = altered["loaded_shared_modules"][0]
                    elif mutation == "nonlocal":
                        altered["loaded_shared_modules"][0]["path"] = str(package / "other.dll")
                    else:
                        altered["loaded_shared_modules"][0]["file_sha256"] = "0" * 64
                    completed.stdout = json.dumps(altered)
                    with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                        verify_frozen_shared_runtime(package, release, "0.16.10")

    def test_rejects_duplicate_dll_payload_even_if_release_manifest_records_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp).resolve()
            _, release, _ = package_fixture(package)
            (package / "LIBUSB-1.0.DLL").write_bytes(b"libusb-1.0.dll")
            release.write_text(json.dumps(build_manifest(package, "CPU", "0.16.10")))
            with patch("scripts.verify_sdr_frozen_shared_runtime.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    verify_frozen_shared_runtime(package, release, "0.16.10")
                run.assert_not_called()

    def test_rejects_baseline_missing_manifest_and_wrong_native_before_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp).resolve()
            module, release, manifest = package_fixture(package)
            native_manifest = module.parent / "native_build_manifest.json"
            with patch("scripts.verify_sdr_frozen_shared_runtime.subprocess.run") as run:
                for content in (None, {k: v for k, v in manifest.items() if not k.startswith("hackrf_")}):
                    if content is None:
                        native_manifest.unlink()
                    else:
                        native_manifest.write_text(json.dumps(content))
                    release.write_text(json.dumps(build_manifest(package, "CPU", "0.16.10")))
                    with self.assertRaises(ValueError):
                        verify_frozen_shared_runtime(package, release, "0.16.10")
                native_manifest.write_text(json.dumps(manifest))
                module.write_bytes(b"wrong native")
                release.write_text(json.dumps(build_manifest(package, "CPU", "0.16.10")))
                with self.assertRaisesRegex(ValueError, "artifact_sha256"):
                    verify_frozen_shared_runtime(package, release, "0.16.10")
                run.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows metadata DLL ownership injection")
class MetadataHoldTests(unittest.TestCase):
    def test_load_only_absolute_reference_releases_once_on_success_and_body_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "libiio.dll"
            path.touch()
            kernel = SimpleNamespace(LoadLibraryExW=Mock(return_value=123), FreeLibrary=Mock(return_value=1))
            with patch.object(runtime.ctypes, "WinDLL", return_value=kernel):
                with runtime.hold_package_libiio_metadata(path):
                    kernel.FreeLibrary.assert_not_called()
                kernel.LoadLibraryExW.assert_called_once_with(str(path.resolve()), None, 0x1100)
                kernel.FreeLibrary.assert_called_once_with(123)
                kernel.FreeLibrary.reset_mock()
                with self.assertRaisesRegex(ValueError, "body"), runtime.hold_package_libiio_metadata(path):
                    raise ValueError("body")
                kernel.FreeLibrary.assert_called_once_with(123)

    def test_failed_load_or_release_is_not_success_and_no_handle_retry_occurs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "libiio.dll"
            path.touch()
            kernel = SimpleNamespace(LoadLibraryExW=Mock(return_value=0), FreeLibrary=Mock(return_value=0))
            with patch.object(runtime.ctypes, "WinDLL", return_value=kernel):
                with self.assertRaisesRegex(RuntimeError, "load failed"), runtime.hold_package_libiio_metadata(path):
                    self.fail("body must not run")
                kernel.FreeLibrary.assert_not_called()
                kernel.LoadLibraryExW.return_value = 123
                with (self.assertRaisesRegex(RuntimeError, "release was not confirmed"),
                      runtime.hold_package_libiio_metadata(path)):
                    pass
                kernel.FreeLibrary.assert_called_once_with(123)


class OfficialPackagePipelineTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "PowerShell pipeline admission")
    def test_rejects_incomplete_cuda_untagged_skip_and_missing_sdk_before_python(self):
        for args, reason in (
            (["-HackrfLibrary", "missing.lib"], "tagged full CPU"),
            (["-OutputTag", "guard-test", "-Lane", "CUDA", "-HackrfLibrary", "missing.lib"], "tagged full CPU"),
            *([(["-OutputTag", "guard-test", "-HackrfLibrary", "missing.lib", flag], "tagged full CPU")
               for flag in ("-SkipNative", "-SkipFreeze", "-SkipTests")]),
            (["-OutputTag", "guard-test", "-HackrfLibrary", "missing.lib"], "Both Hackrf"),
            (["-OutputTag", "guard-test", "-HackrfLibrary", "missing.lib", "-HackrfIncludeDirectory", "missing"],
             "header/import library is missing"),
        ):
            with self.subTest(args=args):
                result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(ROOT / "build_sdr_release.ps1"),
                                         *args], capture_output=True, text=True, timeout=15, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
        self.assertFalse((ROOT / "dist/SDRNativeMonitoring-CPU-guard-test").exists())

    def test_script_freezes_stage_only_and_exact_manifest_with_one_libusb(self):
        source = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        freeze = (ROOT / "scripts/freeze_sdr_official.py").read_text(encoding="utf-8")
        self.assertIn("-StageOnly -HackrfIncludeDirectory", source)
        self.assertIn("windows-msvc-cpu-hackrf\\python", source)
        self.assertIn("freeze_sdr_official.py", source)
        self.assertIn('"sdr_monitor._sdr_native"', freeze)
        self.assertIn('str(manifest_path) + ";sdr_monitor"', freeze)
        self.assertIn('("hackrf.dll", "pthreadVC3.dll")', freeze)
        self.assertIn("existing packages are preserved", source)
        self.assertGreater(source.index("verify_sdr_frozen_shared_runtime.py"),
                           source.index("verify_sdr_frozen_tinysa_runtime.py"))


if __name__ == "__main__":
    unittest.main()
