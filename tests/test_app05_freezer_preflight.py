"""Actual installed generator and fail-closed spec policy, no executable build."""

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.preflight_sdr_freezer import verify_freezer


class FreezerPreflightTests(unittest.TestCase):
    def test_real_generator_keeps_syntax_stdout_and_hide_early(self):
        report = verify_freezer(Path(__file__).resolve().parents[1] / "main_sdr.py")
        self.assertEqual(report["spec_policy"], "console+hide-early")
        self.assertTrue(report["pyinstaller_version"])

    def test_invalid_syntax_missing_or_changed_policy_fail_closed(self):
        parse = ast.parse
        for source in ("EXE(console=True, hide_console=''hide-early'')", "EXE()",
                       "EXE(console=False, hide_console='hide-early')", "EXE(console=True, hide_console='hide-late')"):
            with self.subTest(source=source), patch("scripts.preflight_sdr_freezer.ast.parse",
                    side_effect=lambda *args, _source=source, **kwargs: parse(_source)), \
                    self.assertRaises((SyntaxError, ValueError)):
                verify_freezer(Path(__file__).resolve().parents[1] / "main_sdr.py")

    def test_build_checks_freezer_before_native(self):
        source = (Path(__file__).resolve().parents[1] / "build_sdr_release.ps1").read_text(encoding="utf-8")
        self.assertLess(source.index("preflight_sdr_freezer.py"),
                        source.index('& (Join-Path $repoRoot "build_native_sdr.ps1")'))
