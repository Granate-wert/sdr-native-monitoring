"""Qualify an official HackRF/Pluto package without SDK init, Discover or RX."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.preflight_sdr_native_build import _file_sha256, _read_manifest, validate_manifest
from scripts.preflight_sdr_release import verify_manifest
from sdr_monitor.frozen_shared_runtime import REQUIRED_LOADED, SHARED_COMPONENTS
from sdr_monitor.libiio_runtime import LIBIIO_RUNTIME_COMPONENTS


def verify_frozen_shared_runtime(package_dir: Path, manifest_path: Path, version: str) -> dict[str, object]:
    package = package_dir.resolve(strict=True)
    verify_manifest(package, manifest_path, "CPU", version)
    runtime_dir = package / "_internal" / "sdr_monitor"
    native_modules = tuple(package.rglob("_sdr_native*.pyd"))
    if len(native_modules) != 1 or native_modules[0].parent != runtime_dir:
        raise ValueError("official package requires exactly one canonical native artifact")
    native = native_modules[0]
    manifests = tuple(package.rglob("native_build_manifest.json"))
    if manifests != (runtime_dir / "native_build_manifest.json",):
        raise ValueError("official package requires exactly one sibling native manifest")
    native_manifest = _read_manifest(manifests[0])
    validate_manifest(native, native_manifest, expected_cuda=False)
    if native_manifest.get("hackrf_official_compiled") is not True:
        raise ValueError("official HackRF package contains a baseline manifest")
    files = {}
    # Reject hidden root/Qt duplicate DLL payloads as well as wrong SDK bytes.
    for name in SHARED_COMPONENTS:
        matches = tuple(path for path in package.rglob("*") if path.is_file() and path.name.casefold() == name.casefold())
        if len(matches) != 1 or matches[0] != runtime_dir / name:
            raise ValueError("shared package dependency missing, duplicate or misplaced: " + name)
        files[name] = _file_sha256(matches[0])
    with (package / "shared_runtime_inputs.json").open("rb") as stream:
        encoded = stream.read(16_385)
    if len(encoded) > 16_384:
        raise ValueError("shared runtime input report exceeds bound")
    inputs = json.loads(encoded)
    if (not isinstance(inputs, dict) or inputs.get("schema") != "app06-shared-libusb-preflight-v1"
            or inputs.get("passed") is not True or inputs.get("official_hackrf") is not True
            or inputs.get("source_commit") != native_manifest.get("source_commit")
            or inputs.get("libiio_runtime_sha256") != {name: files[name] for name in LIBIIO_RUNTIME_COMPONENTS}
            or inputs.get("selected_libusb_sha256") != files["libusb-1.0.dll"]
            or inputs.get("hackrf_libusb_sha256") != files["libusb-1.0.dll"]):
        raise ValueError("frozen runtime payload differs from admitted pre-freeze inputs")
    environment = dict(os.environ)
    environment["LIBIIO_DLL_PATH"] = str(package / "external-runtime-must-not-be-loaded.dll")
    environment["SDR_AUTO_DISCOVER"] = "0"
    # A successful package cannot rely on the developer's SDK/IIO PATH.
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
    completed = subprocess.run(
        [str(package / "SDRNativeMonitoring.exe"), "--verify-packaged-shared-runtime"],
        cwd=package, env=environment, capture_output=True, text=True, check=False, timeout=30,
    )
    if completed.returncode:
        raise ValueError("frozen official shared runtime load-only command failed: " + completed.stdout + completed.stderr)
    verdict = json.loads(completed.stdout)
    expected = {
        "schema": "sdr-frozen-shared-runtime-v1", "native_module": "sdr_monitor._sdr_native",
        "hackrf_official_compiled": True, "hackrf_factory_contract_version": 2, "cuda_compiled": False,
        "libiio_available": True, "metadata_hold_released": True, "factory_constructed": False, "sdk_initialized": False,
        "discovery_attempted": False, "rx_attempted": False,
    }
    if not isinstance(verdict, dict) or any(type(verdict.get(k)) is not type(v) or verdict.get(k) != v
                                            for k, v in expected.items()):
        raise ValueError("frozen shared runtime result does not match its load-only contract")
    dsp_version = native_manifest.get("hackrf_dsp_profile_contract_version")
    if (dsp_version is not None and (type(dsp_version) is not int or dsp_version != 1)
            or type(verdict.get("hackrf_dsp_profile_contract_version")) is not type(dsp_version)
            or verdict.get("hackrf_dsp_profile_contract_version") != dsp_version):
        raise ValueError("frozen optional DSP profile contract differs from its native manifest")
    persistence_version = native_manifest.get("hackrf_persistence_contract_version")
    if (persistence_version is not None and (type(persistence_version) is not int or persistence_version != 1)
            or type(verdict.get("hackrf_persistence_contract_version")) is not type(persistence_version)
            or verdict.get("hackrf_persistence_contract_version") != persistence_version):
        raise ValueError("frozen optional persistence contract differs from its native manifest")
    sweep_bridge = native_manifest.get("hackrf_sweep_bridge_contract_version")
    geometry = native_manifest.get("sweep_geometry_contract_version")
    capacity = native_manifest.get("sweep_max_segments")
    budget = native_manifest.get("sweep_max_reduced_bytes")
    if (geometry is not None or capacity is not None or budget is not None) and (
            type(geometry) is not int or geometry != 1 or type(capacity) is not int or capacity != 2048
            or type(budget) is not int or budget != 134217728
            or type(verdict.get("sweep_geometry_contract_version")) is not int
            or verdict.get("sweep_geometry_contract_version") != geometry
            or type(verdict.get("sweep_max_segments")) is not int
            or verdict.get("sweep_max_segments") != capacity
            or type(verdict.get("sweep_max_reduced_bytes")) is not int
            or verdict.get("sweep_max_reduced_bytes") != budget):
        raise ValueError("Frozen Sweep geometry differs from its paired native manifest")
    sweep_factory = native_manifest.get("hackrf_sweep_factory_contract_version")
    if ((sweep_bridge is not None or sweep_factory is not None)
            and (type(sweep_bridge) is not int or sweep_bridge != 1
                 or type(sweep_factory) is not int or sweep_factory != 1)
            or type(verdict.get("hackrf_sweep_bridge_contract_version")) is not type(sweep_bridge)
            or verdict.get("hackrf_sweep_bridge_contract_version") != sweep_bridge
            or type(verdict.get("hackrf_sweep_factory_contract_version")) is not type(sweep_factory)
            or verdict.get("hackrf_sweep_factory_contract_version") != sweep_factory):
        raise ValueError("frozen optional Sweep bridge/factory contract differs from its native manifest")
    if (Path(verdict.get("native_path", "")).resolve() != native
            or verdict.get("native_sha256") != _file_sha256(native)
            or verdict.get("native_source_commit") != native_manifest.get("source_commit")
            or verdict.get("runtime_file_sha256") != files):
        raise ValueError("frozen shared runtime native/file identity differs from package")
    loaded = verdict.get("loaded_shared_modules")
    if not isinstance(loaded, list) or not 4 <= len(loaded) <= len(SHARED_COMPONENTS):
        raise ValueError("frozen shared runtime loaded inventory is invalid")
    seen: set[str] = set()
    for item in loaded:
        if not isinstance(item, dict):
            raise TypeError("frozen shared runtime loaded record is invalid")
        loaded_name = item.get("name")
        if (not isinstance(loaded_name, str) or loaded_name not in files or loaded_name in seen
                or Path(item.get("path", "")).resolve() != runtime_dir / loaded_name
                or item.get("file_sha256") != files[loaded_name]):
            raise ValueError("frozen shared runtime dependency is duplicate, nonlocal or mismatched")
        seen.add(loaded_name)
    if not REQUIRED_LOADED <= seen:
        raise ValueError("frozen shared runtime required dependencies were not loaded")
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(verify_frozen_shared_runtime(args.package_dir, args.manifest, args.version), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
