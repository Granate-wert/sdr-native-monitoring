"""Fail-closed verifier for the R12-I frozen app-local libiio loader gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


LIBIIO_RUNTIME_COMPONENTS = (
    "libiio.dll",
    "libserialport-0.dll",
    "libusb-1.0.dll",
    "libxml2-2.dll",
    "libiconv-2.dll",
    "liblzma-5.dll",
    "zlib1.dll",
)


def verify_frozen_libiio_runtime(package_dir: Path) -> dict[str, object]:
    """Require an exact package-local DLL closure and load-only EXE verdict."""

    root = package_dir.resolve()
    executable = root / "SDRNativeMonitoring.exe"
    if not executable.is_file():
        raise ValueError("frozen SDR executable is missing")
    runtime_dir = root / "_internal" / "sdr_monitor"
    missing = [name for name in LIBIIO_RUNTIME_COMPONENTS if not (runtime_dir / name).is_file()]
    if missing:
        raise ValueError("frozen libiio runtime components are missing: " + ", ".join(missing))

    environment = os.environ.copy()
    # The EXE must replace, never honor, an external runtime-path override.
    environment["LIBIIO_DLL_PATH"] = str(root / "external-runtime-must-not-be-loaded.dll")
    completed = subprocess.run(
        [str(executable), "--verify-packaged-libiio-runtime"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("frozen libiio load-only command failed: " + completed.stdout + completed.stderr)
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("frozen libiio load-only command did not emit JSON") from error
    if not isinstance(observed, dict):
        raise ValueError("frozen libiio load-only result must be a JSON object")
    expected = {
        "libiio_available": True,
        "library_package_local": True,
        "pluto_compiled": True,
        "runtime_component_count": len(LIBIIO_RUNTIME_COMPONENTS),
    }
    mismatches = [key for key, value in expected.items() if observed.get(key) != value]
    if mismatches:
        raise ValueError("frozen libiio load-only result rejected: " + ", ".join(mismatches))
    for key in ("libiio_major", "libiio_minor"):
        if not isinstance(observed.get(key), int) or observed[key] < 0:
            raise ValueError("frozen libiio load-only result lacks " + key)
    return {key: observed[key] for key in (*expected, "libiio_major", "libiio_minor")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify_frozen_libiio_runtime(args.package_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
