"""Verify the frozen tinySA UI/serial import closure without discovery or I/O."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def verify_frozen_tinysa_runtime(package_dir: Path) -> dict[str, object]:
    root = package_dir.resolve()
    executable = root / "SDRNativeMonitoring.exe"
    if not executable.is_file():
        raise ValueError("frozen SDR executable is missing")
    environment = os.environ.copy()
    environment["SDR_AUTO_DISCOVER"] = "0"
    completed = subprocess.run(
        [str(executable), "--verify-packaged-tinysa-runtime"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(
            "frozen tinySA runtime verification failed: "
            + completed.stdout
            + completed.stderr
        )
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("frozen tinySA runtime command did not emit JSON") from error
    if not isinstance(observed, dict):
        raise ValueError("frozen tinySA runtime result must be a JSON object")
    expected = {
        "backend_constructed": True,
        "device_discovery_invoked": False,
        "pyside6_available": True,
        "pyserial_available": True,
        "serial_port_opened": False,
    }
    mismatches = [key for key, value in expected.items() if observed.get(key) != value]
    for version_key in ("pyside6_version", "pyserial_version"):
        value = observed.get(version_key)
        if not isinstance(value, str) or not value.strip() or len(value) > 64:
            mismatches.append(version_key)
    if mismatches:
        raise ValueError(
            "frozen tinySA runtime result rejected: " + ", ".join(mismatches)
        )
    return {
        **expected,
        "pyside6_version": observed["pyside6_version"],
        "pyserial_version": observed["pyserial_version"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        result = verify_frozen_tinysa_runtime(arguments.package_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
