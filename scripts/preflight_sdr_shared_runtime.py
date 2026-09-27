"""Validate one shared libusb payload before freezing Pluto + official HackRF.

No DLL load, discovery, RX, device configuration or system-library change.
Exact bundle identity is necessary, not physical/ABI/package acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.preflight_sdr_native_build import _file_sha256, _read_manifest, validate_manifest
from sdr_monitor.libiio_runtime import LIBIIO_RUNTIME_COMPONENTS


def validate_shared_runtime(module: Path, manifest: dict, libiio_directory: Path, *, expected_cuda: bool) -> dict:
    """Never silently overwrite two differently hashed libusb DLLs.

    The frozen payload already chooses its libusb from libiio_directory. An
    official HackRF extension additionally requires its exact admitted SDK
    bundle. Both must select the same bytes, not merely the same basename.
    """
    validate_manifest(module, manifest, expected_cuda=expected_cuda)
    missing = [name for name in LIBIIO_RUNTIME_COMPONENTS if not (libiio_directory / name).is_file()]
    if missing:
        raise ValueError("selected libiio runtime closure missing: " + ", ".join(missing))
    selected = libiio_directory / "libusb-1.0.dll"
    selected_hash = _file_sha256(selected)
    result = {
        "passed": True,
        "selected_libusb": str(selected.resolve()),
        "selected_libusb_sha256": selected_hash,
        "official_hackrf": manifest.get("hackrf_official_compiled") is True,
        "libiio_runtime_sha256": {name: _file_sha256(libiio_directory / name) for name in LIBIIO_RUNTIME_COMPONENTS},
        "scope": "Static exact-bundle admission only; no DLL load, ABI, RX or release acceptance.",
    }
    if result["official_hackrf"]:
        expected_hash = manifest["hackrf_runtime_sha256"]["libusb-1.0.dll"]
        if selected_hash != expected_hash:
            raise ValueError(
                "shared libusb collision: libiio payload differs from the admitted HackRF SDK; select one explicitly verified bundle before freezing"
            )
        result["hackrf_libusb_sha256"] = expected_hash
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--libiio-directory", type=Path, required=True)
    parser.add_argument("--lane", choices=("CPU", "CUDA"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("a new output path is required")
    try:
        report = validate_shared_runtime(
            args.module.resolve(),
            _read_manifest(args.manifest.resolve()),
            args.libiio_directory.resolve(),
            expected_cuda=args.lane == "CUDA",
        )
    except (OSError, ValueError) as error:
        report = {"passed": False, "failure": str(error), "scope": "Static admission; no DLL was loaded."}
    report.update(
        schema="app06-shared-libusb-preflight-v1",
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        tracked_dirty_paths=subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
        ).splitlines(),
        module=str(args.module.resolve()),
        manifest=str(args.manifest.resolve()),
        libiio_directory=str(args.libiio_directory.resolve()),
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
