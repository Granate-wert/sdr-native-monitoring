"""S00 product boundary — import graph and manifest tests."""

from __future__ import annotations

import os
import re
import unittest


_SDR_FORBIDDEN_PREFIXES = (
    "esw_dfl.parser",
    "esw_dfl.spectrogram",
    "esw_dfl.domain",
    "esw_dfl.models",
    "esw_dfl.power_measurements",
    "esw_dfl.time_gated_power",
    "esw_dfl.heatmap",
    "esw_dfl.cli",
    "esw_dfl.gui",
    "esw_dfl._sgram_native",
)

_UI_PACKAGE = re.compile(r"^esw_dfl[\\/]ui[\\/]")


def _python_files(root: str) -> list[str]:
    result = []
    for dirpath, _, filenames in os.walk(root):
        for entry in filenames:
            if entry.endswith(".py"):
                result.append(os.path.join(dirpath, entry))
    return sorted(result)


def _imports(filename: str) -> set[str]:
    content = open(filename, encoding="utf-8", errors="replace").read()
    imports: set[str] = set()
    for match in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w\.]+)", content, re.M):
        value = match.group(1)
        if value.startswith("esw_dfl"):
            imports.add(value)
    return imports


class S00BoundaryTests(unittest.TestCase):
    def test_sdr_tree_does_not_import_dfl_internals(self) -> None:
        targets = [
            path for path in _python_files("esw_dfl")
            if re.search(r"esw_dfl[\\/]sdr[\\/]", path)
            and not path.endswith("__init__.py")
        ]
        self.assertTrue(targets, "no SDR modules found")
        violations = []
        for path in targets:
            for imported in _imports(path):
                if imported.startswith(_SDR_FORBIDDEN_PREFIXES):
                    violations.append(f"{path} imports {imported}")
        self.assertEqual(violations, [])

    def test_ui_does_not_import_dfl_parser(self) -> None:
        targets = [p for p in _python_files("esw_dfl/ui") if not p.endswith("__init__.py")]
        violations = []
        for path in targets:
            for imported in _imports(path):
                if imported.startswith(("esw_dfl.parser", "esw_dfl.spectrogram", "esw_dfl.domain", "esw_dfl.models")):
                    violations.append(f"{path} imports {imported}")
        self.assertEqual(violations, [])

    def test_sdr_mvi_manifest_excludes_sgram_native(self) -> None:
        candidates = [
            "native/sdr_core/CMakePresets.json",
            "pyproject.toml",
            "README.md",
        ]
        for manifest in candidates:
            self.assertTrue(os.path.isfile(manifest), f"missing {manifest}")
            content = open(manifest, encoding="utf-8", errors="replace").read()
            self.assertNotIn("_sgram_native", content, f"{manifest} mentions _sgram_native")

    def test_new_sdr_entry_point_exists(self) -> None:
        self.assertTrue(os.path.isfile("main_sdr.py"), "main_sdr.py missing")
        content = open("main_sdr.py", encoding="utf-8").read()
        self.assertIn("sdr_native_monitoring", content)
        self.assertNotIn("_sgram_native", content)

    def test_legacy_entry_point_still_exists(self) -> None:
        self.assertTrue(os.path.isfile("main.py"), "legacy main.py missing")


if __name__ == "__main__":
    unittest.main()
