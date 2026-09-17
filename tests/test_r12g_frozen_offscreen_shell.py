"""R12-G tests for the packaged offscreen AppShell lifecycle gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class R12GFrozenOffscreenShellTests(unittest.TestCase):
    def test_source_offscreen_shell_is_analyzer_no_device_and_closed(self) -> None:
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["SDR_AUTO_DISCOVER"] = "1"
        completed = subprocess.run(
            [sys.executable, "main_sdr.py", "--offscreen-shell-smoke"],
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
                "live_service": "UnavailableLiveService",
                "qt_platform": "offscreen",
                "startup_visible": True,
                "window_title": "SDR Native Monitoring — UI V2",
                "ui_mode": "v2",
                "shell_class": "AppShellV2",
                "workspace": "analyzer",
            },
        )

    def test_source_refuses_a_preselected_visible_qt_platform(self) -> None:
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "windows"
        completed = subprocess.run(
            [sys.executable, "main_sdr.py", "--offscreen-shell-smoke"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refuses a non-offscreen Qt platform", completed.stderr)

    def test_no_device_composition_and_early_command_branch_are_explicit(self) -> None:
        main = (ROOT / "sdr_monitor/main.py").read_text(encoding="utf-8")
        services = (ROOT / "sdr_monitor/services/sdr_application_services.py").read_text(encoding="utf-8")
        smoke_start = services.index("def build_offscreen_smoke_sdr_services")
        smoke_section = services[smoke_start : services.index("__all__", smoke_start)]

        self.assertLess(
            main.index("if arguments.offscreen_shell_smoke:"),
            main.index("logger = _configure_logging()"),
        )
        self.assertIn("UnavailableLiveService()", smoke_section)
        self.assertIn("UnavailableSweepService()", smoke_section)
        self.assertNotIn("build_optional_native_live_service", smoke_section)

    def test_release_build_runs_shell_verifier_after_native_artifact_verifier(self) -> None:
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify_sdr_frozen_shell.py").read_text(encoding="utf-8")

        self.assertIn("verify_sdr_frozen_shell.py", release)
        self.assertGreater(
            release.index("verify_sdr_frozen_shell.py"),
            release.index("verify_sdr_frozen_package.py"),
        )
        for expected in (
            '"qt_platform": "offscreen"',
            '"workspace": "analyzer"',
            '"live_service": "UnavailableLiveService"',
            '"closed": True',
        ):
            self.assertIn(expected, verifier)


if __name__ == "__main__":
    unittest.main()
