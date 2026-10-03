"""Actual installed generator and fail-closed spec policy, no executable build."""

import ast
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from scripts.freeze_sdr_official import freeze
from scripts.preflight_sdr_freezer import required_freezer_version, verify_freezer, verify_freezer_version


class FreezerPreflightTests(unittest.TestCase):
    def test_real_generator_keeps_syntax_stdout_and_hide_early(self):
        report = verify_freezer(Path(__file__).resolve().parents[1] / "main_sdr.py")
        self.assertEqual(report["spec_policy"], "console+hide-early")
        self.assertEqual(report["pyinstaller_version"], required_freezer_version())
        self.assertEqual(report["required_pyinstaller_version"], required_freezer_version())
        self.assertEqual(report["python_executable"], sys.executable)
        self.assertTrue(Path(report["pyinstaller_module"]).is_file())
        self.assertEqual(report["pin_source"], "pyproject.toml:project.optional-dependencies.dev")

    def test_version_drift_refuses_before_generator_or_temporary_artifacts(self):
        import PyInstaller
        for actual in ("6.21.0", "6.22.0", "6.22.4"):
            with (self.subTest(actual=actual), patch.object(PyInstaller, "__version__", actual),
                  patch("PyInstaller.building.makespec.main") as generate,
                  patch("scripts.preflight_sdr_freezer.TemporaryDirectory") as temporary,
                  self.assertRaisesRegex(ValueError, f"required {required_freezer_version()}, observed {actual}")):
                verify_freezer(Path("main_sdr.py"))
            generate.assert_not_called()
            temporary.assert_not_called()

    def test_missing_ambiguous_or_non_exact_pin_refuses(self):
        invalid = ([], ["pyinstaller>=6.22.3"], ["pyinstaller==6.22.3", "PyInstaller==6.22.3"],
                   ["pyinstaller==6.22.3; python_version >= '3.13'"], ["pyinstaller==6.22.*"],
                   ["pyinstaller[extra]==6.22.3"], [None], "pyinstaller==6.22.3")
        for dev in invalid:
            project = {"project": {"optional-dependencies": {"dev": dev}}}
            with (self.subTest(dev=dev), patch("scripts.preflight_sdr_freezer.tomllib.loads", return_value=project),
                  self.assertRaises(ValueError)):
                verify_freezer_version()
        for project in ({"project": []}, {"project": {"optional-dependencies": []}}):
            with (self.subTest(project=project),
                  patch("scripts.preflight_sdr_freezer.tomllib.loads", return_value=project),
                  self.assertRaises(ValueError)):
                verify_freezer_version()

    def test_canonical_pin_not_a_second_hardcoded_version(self):
        import PyInstaller
        project = {"project": {"optional-dependencies": {"dev": ["PyInstaller==9.8.7"]}}}
        with (patch("scripts.preflight_sdr_freezer.tomllib.loads", return_value=project),
              patch.object(PyInstaller, "__version__", "9.8.7")):
            self.assertEqual(verify_freezer_version()["required_pyinstaller_version"], "9.8.7")

    def test_direct_official_freezer_refuses_before_inputs_generator_or_subprocess(self):
        import PyInstaller
        with (patch.object(PyInstaller, "__version__", "6.21.0"),
              patch("scripts.freeze_sdr_official._read_manifest") as manifest,
              patch("PyInstaller.building.makespec.main") as generate,
              patch("scripts.freeze_sdr_official.subprocess.run") as run,
              self.assertRaisesRegex(ValueError, "version mismatch")):
            freeze(*(Path("not-created") for _ in range(5)))
        manifest.assert_not_called()
        generate.assert_not_called()
        run.assert_not_called()

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
