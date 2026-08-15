from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT / "native/sdr_core/bindings/hackrf_binding.cpp"
MODULE = ROOT / "native/sdr_core/bindings/python_module.cpp"
CMAKE = ROOT / "native/sdr_core/CMakeLists.txt"


class R11LHackrfControlBindingSourceTests(unittest.TestCase):
    def test_canonical_extension_binds_only_the_coarse_control_surface(self) -> None:
        text = BINDING.read_text(encoding="utf-8")
        self.assertIn('"HackrfRuntimeDspControl"', text)
        for allowed in ('"poll_spectrum_frames"', '"metrics"', '"stop"'):
            with self.subTest(allowed=allowed):
                self.assertIn(allowed, text)
        self.assertNotIn("py::init", text)
        for forbidden in ('"start"', '"configure"', '"retune"', '"raw_iq"', '"ci8"'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_fixture_is_explicitly_private_and_has_no_device_sdk_or_product_path(self) -> None:
        text = BINDING.read_text(encoding="utf-8").lower()
        self.assertIn('"_make_test_hackrf_runtime_dsp_control"', text)
        self.assertIn("class r11ltestruntime", text)
        self.assertIn("#if defined(sdr_core_enable_test_hooks)", text)
        for forbidden in (
            "hackrf.h",
            "hackrf.dll",
            "official",
            "pyside",
            "qwidget",
            "recording",
            "sweep",
            "start_tx",
            "firmware",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_stop_is_bounded_and_releases_the_gil(self) -> None:
        text = BINDING.read_text(encoding="utf-8")
        self.assertIn("r11l_min_stop_timeout_ms = 1U", text)
        self.assertIn("r11l_max_stop_timeout_ms = 5'000U", text)
        self.assertIn("bounded_stop_timeout", text)
        self.assertIn("py::gil_scoped_release release", text)

    def test_binding_is_in_the_one_canonical_native_extension(self) -> None:
        module = MODULE.read_text(encoding="utf-8")
        cmake = CMAKE.read_text(encoding="utf-8")
        self.assertIn('#include "hackrf_binding.hpp"', module)
        self.assertIn("sdr_core::python::bind_hackrf(module);", module)
        self.assertIn("bindings/hackrf_binding.cpp", cmake)
        self.assertIn("sdr_core::sdr_hackrf", cmake)
        self.assertIn("target_compile_definitions(_sdr_native PRIVATE SDR_CORE_ENABLE_TEST_HOOKS=1)", cmake)


if __name__ == "__main__":
    unittest.main()
