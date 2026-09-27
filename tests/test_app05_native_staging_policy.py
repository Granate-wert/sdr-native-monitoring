"""Alternate build lanes cannot silently replace the application's CPU module."""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NativeStagingPolicyTests(unittest.TestCase):
    def test_staged_build_preserves_preflight_and_guards_all_activation(self):
        script = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        guard = script.index('if ($Configuration -eq "Release" -and -not $StageOnly)')
        self.assertLess(script.index('Invoke-Checked -FilePath $PythonExecutable -Arguments $preflightArgs'), guard)
        self.assertGreater(script.index('$activeDir ='), guard)
        self.assertGreater(script.index('verify_sdr_native_active_import.py'), guard)
        self.assertIn('[switch]$StageOnly', script)

    @unittest.skipUnless(os.name == "nt", "PowerShell execution guard is Windows-only")
    def test_conflicting_clean_is_rejected_before_any_tool_or_file_action(self):
        script = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        guard = script.index('if ($StageOnly -and $Clean)')
        self.assertLess(guard, script.index('$repoRoot ='))
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-File", str(ROOT / "build_native_sdr.ps1"),
            "-StageOnly", "-Clean",
        ], capture_output=True, text=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('StageOnly cannot be combined with Clean', result.stderr)
