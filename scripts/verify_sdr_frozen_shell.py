"""Fail-closed verifier for the R12-G frozen offscreen AppShell lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def verify_frozen_offscreen_shell(package_dir: Path) -> dict[str, object]:
    """Run only the packaged private offscreen lifecycle command."""

    root = package_dir.resolve()
    executable = root / "SDRNativeMonitoring.exe"
    if not executable.is_file():
        raise ValueError("frozen SDR executable is missing")
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["SDR_AUTO_DISCOVER"] = "0"
    completed = subprocess.run(
        [str(executable), "--offscreen-shell-smoke"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("frozen offscreen shell command failed: " + completed.stdout + completed.stderr)
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("frozen offscreen shell command did not emit JSON") from error
    if not isinstance(observed, dict):
        raise ValueError("frozen offscreen shell result must be a JSON object")
    expected = {
        "ui_mode": "v2",
        "shell_class": "AppShellV2",
        "qt_platform": "offscreen",
        "workspace": "analyzer",
        "window_title": "SDR Native Monitoring — UI V2",
        "startup_visible": True,
        "closed": True,
        "automatic_discovery_pending": False,
        "live_running": False,
        "live_service": "UnavailableLiveService",
    }
    mismatches = [key for key, value in expected.items() if observed.get(key) != value]
    if mismatches:
        raise ValueError("frozen offscreen shell result rejected: " + ", ".join(mismatches))
    return {key: observed[key] for key in expected}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify_frozen_offscreen_shell(args.package_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
