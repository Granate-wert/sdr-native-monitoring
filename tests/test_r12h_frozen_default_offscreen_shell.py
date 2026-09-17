"""R12-H tests for the frozen default-composition AppShell lifecycle gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class R12HFrozenDefaultOffscreenShellTests(unittest.TestCase):
    def test_source_default_offscreen_shell_is_native_analyzer_and_closed(self) -> None:
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["SDR_AUTO_DISCOVER"] = "1"
        completed = subprocess.run(
            [sys.executable, "main_sdr.py", "--offscreen-default-shell-smoke"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        observed = json.loads(completed.stdout)
        self.assertEqual(
            observed,
            {
                "automatic_discovery_pending": False,
                "closed": True,
                "live_running": False,
                "live_service": "NativeLiveSessionService",
                "native_device_constructed": False,
                "native_engine_constructed": False,
                "pluto_compiled": True,
                "qt_platform": "offscreen",
                "startup_visible": True,
                "window_title": "SDR Native Monitoring — UI V2",
                "ui_mode": "v2",
                "shell_class": "AppShellV2",
                "workspace": "analyzer",
            },
        )

    def test_source_default_smoke_refuses_a_preselected_visible_qt_platform(self) -> None:
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "windows"
        completed = subprocess.run(
            [sys.executable, "main_sdr.py", "--offscreen-default-shell-smoke"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refuses a non-offscreen Qt platform", completed.stderr)

    def test_default_composition_and_early_command_branch_are_explicit(self) -> None:
        main = (ROOT / "sdr_monitor/main.py").read_text(encoding="utf-8")

        self.assertLess(
            main.index("if arguments.offscreen_default_shell_smoke:"),
            main.index("logger = _configure_logging()"),
        )
        default_start = main.index("def _offscreen_default_shell_verdict")
        default_section = main[default_start : main.index("def _run_offscreen_shell", default_start)]
        shared_start = main.index("def _run_offscreen_shell")
        shared_section = main[shared_start : main.index("def _size_window_to_work_area", shared_start)]
        self.assertIn("return _run_offscreen_shell(default_composition=True)", default_section)
        self.assertIn("shell = build_v2_shell(services)", shared_section)
        self.assertNotIn("SDRAppShell", shared_section)
        self.assertIn("NativeLiveSessionService", shared_section)
        self.assertIn('os.environ["SDR_AUTO_DISCOVER"] = "0"', shared_section)
        self.assertIn("previous_qt_platform", shared_section)
        self.assertIn("native_device_constructed", shared_section)
        self.assertIn("native_engine_constructed", shared_section)

    def test_release_build_runs_default_shell_verifier_after_no_device_verifier(self) -> None:
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify_sdr_frozen_default_shell.py").read_text(encoding="utf-8")

        self.assertIn("verify_sdr_frozen_default_shell.py", release)
        self.assertGreater(
            release.index("verify_sdr_frozen_default_shell.py"),
            release.index("verify_sdr_frozen_shell.py"),
        )
        for expected in (
            '"live_service": "NativeLiveSessionService"',
            '"native_device_constructed": False',
            '"native_engine_constructed": False',
            '"pluto_compiled": True',
        ):
            self.assertIn(expected, verifier)


if __name__ == "__main__":
    unittest.main()
