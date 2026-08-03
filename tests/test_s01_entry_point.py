"""S01 entry-point boundary tests."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest


class S01EntryPointTests(unittest.TestCase):
    def test_sdr_monitor_package_imports_without_dfl(self) -> None:
        try:
            del sys.modules["esw_dfl"]
        except KeyError:
            pass
        try:
            del sys.modules["esw_dfl.gui"]
        except KeyError:
            pass
        # Block the DFL package in this interpreter just for the test.
        original = importlib.import_module
        blocked: set[str] = set()

        def importer(name, package=None):
            blocked.add(name)
            if name.startswith("esw_dfl"):
                raise ImportError(f"DFL package blocked: {name}")
            return original(name, package)

        import importlib as _mod
        try:
            import types as _types
            _mod.import_module = importer  # type: ignore[attr-defined]
            import sdr_monitor  # noqa: F401
        finally:
            _mod.import_module = original  # type: ignore[attr-defined]
        self.assertNotIn("esw_dfl", sys.modules, "sdr_monitor must not force-load the DFL root")

    def test_sdr_monitor_entry_point(self) -> None:
        import main_sdr

        self.assertTrue(hasattr(main_sdr, "main"))
        self.assertTrue(callable(main_sdr.main))
        with open("main_sdr.py", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("sdr_native_monitoring", content)

    def test_sdr_monitor_package_attributes(self) -> None:
        import sdr_monitor

        self.assertIsInstance(sdr_monitor.__version__, str)
        self.assertRegex(sdr_monitor.__version__, r"^\d+\.\d+\.\d+$")

    def test_main_py_unchanged(self) -> None:
        """The legacy DFL entry point must remain untouched for now."""

        content = open("main.py", encoding="utf-8").read()
        self.assertIn("esw_dfl.gui", content)


if __name__ == "__main__":
    unittest.main()
