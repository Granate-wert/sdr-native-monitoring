"""Keep CLI verification output while hiding a GUI launch's own console."""

from pathlib import Path
import unittest


class ConsolePolicyTests(unittest.TestCase):
    def test_release_uses_bootloader_hide_early_not_windowed_stdout_removal(self):
        source = (Path(__file__).resolve().parents[1] / "build_sdr_release.ps1").read_text(encoding="utf-8")
        commands = [line for line in source.splitlines() if "& $python -m PyInstaller " in line]
        self.assertEqual(len(commands), 1)
        self.assertIn("--hide-console hide-early", commands[0])
        self.assertNotIn("--windowed", commands[0])
        self.assertNotIn("--noconsole", commands[0])
