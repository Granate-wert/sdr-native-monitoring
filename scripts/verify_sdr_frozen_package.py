"""Fail-closed verifier for a frozen standalone SDR package.

It checks distribution bytes and invokes only the package's metadata-only native
artifact command. No SDR device, UI or product Live operation is entered.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from preflight_sdr_release import verify_manifest


CANONICAL_MODULE_NAME = "sdr_monitor._sdr_native"


def _native_modules(package_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted(path.resolve() for path in package_dir.rglob("_sdr_native*.pyd") if path.is_file()))


def verify_frozen_package(package_dir: Path, manifest_path: Path, lane: str, version: str) -> dict[str, object]:
    """Verify one manifest-covered native module loads from the frozen package."""

    root = package_dir.resolve()
    manifest = verify_manifest(root, manifest_path.resolve(), lane, version)
    executable = root / "SDRNativeMonitoring.exe"
    if not executable.is_file():
        raise ValueError("frozen SDR executable is missing")
    native_modules = _native_modules(root)
    if len(native_modules) != 1:
        raise ValueError(f"expected one packaged native extension, found {len(native_modules)}")

    completed = subprocess.run(
        [str(executable), "--verify-native-artifact"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("frozen native-artifact command failed: " + completed.stdout + completed.stderr)
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("frozen native-artifact command did not emit JSON") from error
    if not isinstance(observed, dict):
        raise ValueError("frozen native-artifact result must be a JSON object")
    if observed.get("native_module") != CANONICAL_MODULE_NAME:
        raise ValueError("frozen native module identity is not canonical")
    native_path = Path(str(observed.get("native_path", ""))).resolve()
    if native_path != native_modules[0]:
        raise ValueError("frozen native module did not load the manifest-covered artifact")
    try:
        native_path.relative_to(root)
    except ValueError as error:
        raise ValueError("frozen native module loaded outside the package") from error
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise ValueError("verified release manifest files must be a list")
    expected_cuda = lane == "CUDA"
    if observed.get("cuda_compiled") is not expected_cuda:
        raise ValueError(
            "frozen native module CUDA lane does not match package lane "
            f"(expected cuda_compiled={expected_cuda!r})"
        )
    return {
        "executable": str(executable),
        "native_module": str(native_path),
        "lane": lane,
        "manifest_files": len(manifest_files),
        "schema": observed.get("schema"),
        "schema_version": observed.get("schema_version"),
        "cuda_compiled": expected_cuda,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--lane", choices=("CPU", "CUDA"), required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        result = verify_frozen_package(args.package_dir, args.manifest, args.lane, args.version)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
