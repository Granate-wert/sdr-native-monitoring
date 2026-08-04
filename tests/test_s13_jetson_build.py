"""S13 Jetson build-path and headless-session contract tests."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class S13JetsonBuildTests(unittest.TestCase):
    def test_linux_cuda_path_is_configurable_and_targets_jetson_architecture(self) -> None:
        cmake = (ROOT / "native" / "sdr_core" / "CMakeLists.txt").read_text(encoding="utf-8")
        presets = json.loads((ROOT / "native" / "sdr_core" / "CMakePresets.json").read_text(encoding="utf-8-sig"))
        self.assertNotIn("P08 CUDA backend is currently verified on Windows only", cmake)
        self.assertNotIn("P06 dynamic libiio backend is currently verified on Windows only", cmake)
        self.assertIn("src/pluto/pluto_backend_linux.cpp", cmake)
        self.assertIn("src/pluto/pluto_device_linux.cpp", cmake)
        self.assertIn("CMAKE_DL_LIBS", cmake)
        cuda = next(item for item in presets["configurePresets"] if item["name"] == "linux-aarch64-native-cuda")
        self.assertEqual(cuda["cacheVariables"]["CMAKE_CUDA_ARCHITECTURES"], "87")
        self.assertEqual(cuda["cacheVariables"]["SDR_CORE_ENABLE_PLUTO"], "ON")
        self.assertEqual(cuda["cacheVariables"]["SDR_CORE_BUILD_PYTHON"], "ON")

    def test_linux_loaders_use_runtime_discovery_and_python_abi_is_cp313(self) -> None:
        backend = (ROOT / "native" / "sdr_core" / "src" / "pluto" / "pluto_backend_linux.cpp").read_text(encoding="utf-8")
        cufft = (ROOT / "native" / "sdr_core" / "src" / "cuda" / "cufft_plan_cache.cpp").read_text(encoding="utf-8")
        dependencies = (ROOT / "native" / "sdr_core" / "cmake" / "Dependencies.cmake").read_text(encoding="utf-8")
        toolchain = (ROOT / "native" / "sdr_core" / "cmake" / "toolchains" / "linux-aarch64.cmake").read_text(encoding="utf-8")
        self.assertIn("dlopen", backend)
        self.assertIn("libiio.so", backend)
        self.assertIn("dlopen", cufft)
        self.assertIn("libcufft.so", cufft)
        self.assertIn("find_package(Python 3.13", dependencies)
        self.assertIn("SDR_AARCH64_PYTHON_EXECUTABLE", toolchain)
    def test_headless_app_shell_smoke(self) -> None:
        from PySide6.QtWidgets import QApplication

        from sdr_monitor.ui.app_shell import SDRAppShell, WorkspaceId

        app = QApplication.instance() or QApplication([])
        shell = SDRAppShell()
        shell.resize(1024, 720)
        shell.show()
        app.processEvents()
        self.assertEqual(shell.active_workspace, WorkspaceId.HOME)
        shell.set_active_workspace(WorkspaceId.DIAGNOSTICS)
        app.processEvents()
        self.assertEqual(shell.active_workspace, WorkspaceId.DIAGNOSTICS)
        shell.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
