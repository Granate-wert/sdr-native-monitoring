from __future__ import annotations

import gc
import importlib
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from esw_dfl.sdr import native_api


ROOT = Path(__file__).resolve().parents[1]
NATIVE_SOURCE = ROOT / "native" / "sdr_core"


class NativeApiFallbackTests(unittest.TestCase):
    def test_missing_module_returns_controlled_status(self) -> None:
        status, module = native_api.probe_native("esw_dfl._sdr_native_definitely_missing")
        self.assertFalse(status.available)
        self.assertIsNone(module)
        self.assertEqual(status.build_info, {})
        self.assertIn("ModuleNotFoundError", status.reason or "")

    def test_loader_error_returns_controlled_status(self) -> None:
        def broken_importer(_name: str) -> object:
            raise OSError("simulated loader failure")

        status, module = native_api.probe_native("unused", importer=broken_importer)  # type: ignore[arg-type]
        self.assertFalse(status.available)
        self.assertIsNone(module)
        self.assertIn("simulated loader failure", status.reason or "")

    def test_invalid_build_info_is_not_accepted(self) -> None:
        class InvalidModule:
            @staticmethod
            def build_info() -> dict[str, object]:
                return {"version": "invalid"}

        status, module = native_api.probe_native("unused", importer=lambda _name: InvalidModule())  # type: ignore[arg-type]
        self.assertFalse(status.available)
        self.assertIsNone(module)
        self.assertIn("missing required fields", status.reason or "")

    def test_package_import_is_safe_with_current_availability(self) -> None:
        status = native_api.native_availability()
        self.assertIsInstance(status.available, bool)
        self.assertIsInstance(status.build_info, dict)
        if status.available:
            self.assertIsNone(status.reason)
        else:
            self.assertIsNotNone(status.reason)
            with self.assertRaises(native_api.NativeModuleUnavailableError):
                native_api.require_native()

    def test_common_core_has_no_python_or_windows_headers(self) -> None:
        common_files = list((NATIVE_SOURCE / "include").rglob("*.hpp")) + list(
            (NATIVE_SOURCE / "src" / "core").rglob("*.cpp")
        )
        self.assertGreaterEqual(len(common_files), 4)
        forbidden = ("Python.h", "pybind11", "windows.h", "WinAPI", "DirectX", "PySide6", "pyqtgraph")
        combined = "\n".join(path.read_text(encoding="utf-8") for path in common_files)
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_common_core_has_no_vendor_gpu_types(self) -> None:
        """P08H-00 portability gate: common headers stay vendor-neutral."""
        common_files = list((NATIVE_SOURCE / "include" / "sdr_core").rglob("*.hpp")) + list(
            (NATIVE_SOURCE / "src" / "core").rglob("*.cpp")
        ) + list((NATIVE_SOURCE / "src" / "dsp").rglob("*.cpp"))
        self.assertGreaterEqual(len(common_files), 4)
        forbidden = (
            "cuda_runtime",
            "cuda.h",
            "cufft",
            "cudaStream_t",
            "cufftHandle",
            "CUdevice",
            "hip/hip",
            "hipfft",
            "amdhip",
        )
        for path in common_files:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_public_common_api_exports_no_raw_pointers(self) -> None:
        api = (NATIVE_SOURCE / "include" / "sdr_core" / "api.hpp").read_text(encoding="utf-8")
        self.assertNotIn("*", api)
    def test_required_presets_and_cpu_options_exist(self) -> None:
        presets = (NATIVE_SOURCE / "CMakePresets.json").read_text(encoding="utf-8")
        cmake = (NATIVE_SOURCE / "CMakeLists.txt").read_text(encoding="utf-8")
        for name in ("windows-msvc-cpu", "windows-msvc-cpu-release", "linux-x64-cpu", "linux-aarch64-cpu"):
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', presets)
        for option in (
            "SDR_CORE_BUILD_PYTHON",
            "SDR_CORE_BUILD_TESTS",
            "SDR_CORE_ENABLE_PLUTO",
            "SDR_CORE_ENABLE_CUDA",
            "SDR_CORE_ENABLE_SIMD",
            "SDR_CORE_ENABLE_SANITIZERS",
            "SDR_CORE_ENABLE_PROFILING",
        ):
            with self.subTest(option=option):
                self.assertIn(option, cmake)


@unittest.skipUnless(native_api.native_availability().available, "compiled _sdr_native module is unavailable")
class CompiledNativeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = native_api.require_native()

    def test_build_info_schema_and_cpu_flags(self) -> None:
        info = dict(self.native.build_info())
        self.assertEqual(
            set(info),
            {
                "version",
                "compiler",
                "platform",
                "architecture",
                "build_type",
                "cuda_compiled",
                "pluto_compiled",
            },
        )
        for field in ("version", "compiler", "platform", "architecture", "build_type"):
            self.assertIsInstance(info[field], str)
            self.assertTrue(info[field])
        self.assertIsInstance(info["cuda_compiled"], bool)
        self.assertTrue(info["pluto_compiled"])

    def test_cpu_backend_and_self_test(self) -> None:
        expected = ["cpu", "pluto-libiio"] + (["cuda"] if self.native.build_info()["cuda_compiled"] else [])
        self.assertEqual(list(self.native.available_backends()), expected)
        outcome = dict(self.native.run_self_test())
        self.assertIs(outcome.get("ok"), True)
        self.assertIn("operational", str(outcome.get("message")))

    def test_exception_hierarchy_and_translation(self) -> None:
        base = self.native.SdrNativeError
        for name in (
            "ConfigurationError",
            "BackendUnavailableError",
            "DeviceError",
            "OperationCancelled",
        ):
            with self.subTest(name=name):
                error_type = getattr(self.native, name)
                self.assertTrue(issubclass(error_type, base))
                with self.assertRaises(error_type):
                    self.native._raise_test_error(name)
        with self.assertRaises(base):
            self.native._raise_test_error("SdrNativeError")

    def test_sleep_without_gil_allows_python_thread_progress(self) -> None:
        started = threading.Event()
        stop = threading.Event()
        counter = 0

        def increment() -> None:
            nonlocal counter
            started.set()
            while not stop.is_set():
                counter += 1

        worker = threading.Thread(target=increment, name="p01-gil-probe")
        worker.start()
        self.assertTrue(started.wait(timeout=1.0))
        before = counter
        try:
            self.native.sleep_without_gil(80)
        finally:
            stop.set()
            worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertGreater(counter, before, "Python thread made no progress during the native long call")

    def test_repeated_import_and_cache_eviction_is_safe(self) -> None:
        script = """
import gc
import importlib
import sys
for _ in range(20):
    module = importlib.import_module('esw_dfl._sdr_native')
    assert module.run_self_test()['ok'] is True
    del module
    sys.modules.pop('esw_dfl._sdr_native', None)
    gc.collect()
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_adapter_reload_retains_available_status(self) -> None:
        for _ in range(5):
            reloaded = importlib.reload(native_api)
            self.assertTrue(reloaded.native_availability().available)
            expected = ("cpu", "pluto-libiio") + (("cuda",) if reloaded.build_info()["cuda_compiled"] else ())
            self.assertEqual(reloaded.available_backends(), expected)
        gc.collect()


if __name__ == "__main__":
    unittest.main()
