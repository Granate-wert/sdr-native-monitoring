"""S01 entry-point tests for the standalone SDR product."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class S01EntryPointTests(unittest.TestCase):
    def test_console_script_and_module_entry_exist(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('sdr-native-monitoring = "sdr_monitor.main:main"', pyproject)
        self.assertTrue((ROOT / "sdr_monitor" / "__main__.py").is_file())

    def test_module_version_smoke(self) -> None:
        result = subprocess.run([sys.executable, "-m", "sdr_monitor", "--version"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sdr-native-monitoring", result.stdout)

    def test_offscreen_shell_start_and_close_without_dfl(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from sdr_monitor.ui.app_shell import SDRAppShell

        app = QApplication.instance() or QApplication([])
        shell = SDRAppShell()
        shell.show()
        app.processEvents()
        self.assertEqual(shell.windowTitle(), "SDR Native Monitoring")
        shell.close()
        shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
