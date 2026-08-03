"""S00 architecture boundary tests for the standalone SDR product."""

from __future__ import annotations

import builtins
import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN = ("esw_dfl", "_sgram_native")


def _standalone_sources() -> tuple[pathlib.Path, ...]:
    return tuple(sorted((ROOT / "sdr_monitor").rglob("*.py"))) + (ROOT / "main_sdr.py",)


class S00BoundaryTests(unittest.TestCase):
    def test_standalone_sources_do_not_reference_legacy_product(self) -> None:
        violations = [str(path.relative_to(ROOT)) for path in _standalone_sources() if any(item in path.read_text(encoding="utf-8") for item in FORBIDDEN)]
        self.assertEqual(violations, [])

    def test_sdr_build_script_excludes_dfl_product_and_spectrogram_decoder(self) -> None:
        build_script = (ROOT / "build_sdr_exe.bat").read_text(encoding="utf-8")
        self.assertIn("--exclude-module esw_dfl", build_script)
        self.assertIn("--exclude-module olefile", build_script)
        self.assertNotIn("_sgram_native", build_script)

    def test_standalone_imports_with_legacy_package_blocked(self) -> None:
        original_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "esw_dfl" or name.startswith("esw_dfl."):
                raise ImportError("legacy DFL package is unavailable")
            return original_import(name, *args, **kwargs)

        prior = {name: module for name, module in tuple(sys.modules.items()) if name == "sdr_monitor" or name.startswith("sdr_monitor.")}
        for name in prior:
            sys.modules.pop(name, None)
        try:
            builtins.__import__ = guarded
            importlib.import_module("sdr_monitor")
            importlib.import_module("sdr_monitor.services")
            importlib.import_module("sdr_monitor.ui.app_shell")
        finally:
            builtins.__import__ = original_import
            for name in tuple(sys.modules):
                if name == "sdr_monitor" or name.startswith("sdr_monitor."):
                    sys.modules.pop(name, None)
            sys.modules.update(prior)

    def test_legacy_entry_point_remains_separate(self) -> None:
        self.assertTrue((ROOT / "main.py").is_file())
        self.assertIn("esw_dfl", (ROOT / "main.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
