"""Statically verify manifest-bound external RTL payloads in a frozen package.

Never launches the executable, loads the vendor SDK, discovers USB, or enters RX.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.preflight_sdr_native_build import _file_sha256, _read_manifest, validate_manifest
from scripts.preflight_sdr_release import verify_manifest
from scripts.preflight_sdr_rtl_runtime import MAX_DLL, MAX_FILES, _pe_image, _system_import


def _json(path: Path, maximum: int = 16_384, *, allow_utf8_bom: bool = False) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError("frozen RTL metadata is absent or unsafe")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("frozen RTL metadata exceeds its bound")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate frozen RTL metadata key")
            result[key] = value
        return result

    encoding = "utf-8-sig" if allow_utf8_bom else "utf-8"
    return json.loads(data.decode(encoding), object_pairs_hook=pairs)


def _bounded_file(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("frozen RTL file is absent or unsafe: " + path.name)
    if path.resolve(strict=True).parent != path.parent.resolve(strict=True):
        raise ValueError("frozen RTL file escapes its canonical directory: " + path.name)
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("frozen RTL file exceeds its size bound: " + path.name)
    return data


def verify_frozen_rtl_runtime(package_dir: Path, manifest_path: Path, version: str) -> dict[str, Any]:
    root = package_dir.resolve(strict=True)
    release = verify_manifest(root, manifest_path.resolve(strict=True), "CPU", version)
    runtime_dir = root / "_internal" / "sdr_monitor"
    modules = tuple(path.resolve() for path in root.rglob("_sdr_native*.pyd") if path.is_file())
    if len(modules) != 1 or modules[0].parent != runtime_dir or modules[0].is_symlink() or runtime_dir.is_symlink():
        raise ValueError("RTL package needs exactly one canonical native module")
    native = modules[0]
    native_manifest_path = runtime_dir / "native_build_manifest.json"
    manifests = tuple(path.resolve() for path in root.rglob("native_build_manifest.json") if path.is_file())
    if manifests != (native_manifest_path.resolve(),) or native_manifest_path.is_symlink():
        raise ValueError("RTL package native manifest is missing")
    native_manifest = _read_manifest(native_manifest_path)
    validate_manifest(native, native_manifest, expected_cuda=False)
    if (
        native_manifest.get("rtl_official_compiled") is not True
        or native_manifest.get("rtl_control_contract_version") != 1
    ):
        raise ValueError("package does not contain the RTL contract-1 CPU module")
    report_path = root / "rtl_runtime_inputs.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("RTL frozen input report is missing or unsafe")
    report = _json(report_path)
    required = {
        "schema",
        "passed",
        "source_commit",
        "source_sha256",
        "native_sha256",
        "native_source_commit",
        "native_manifest_sha256",
        "input_manifest_sha256",
        "origin",
        "library",
        "runtime_sha256",
        "notices_sha256",
        "imports",
        "scope",
    }
    if (
        not isinstance(report, dict)
        or set(report) != required
        or report.get("schema") != "app07-rtl-runtime-input-v1"
        or report.get("passed") is not True
        or report.get("scope") != "static input admission only; no ABI, RX, SDK load or license approval"
    ):
        raise ValueError("RTL frozen input report has an invalid contract")
    if (
        report["native_sha256"] != _file_sha256(native)
        or report["native_source_commit"] != native_manifest.get("source_commit")
        or report["native_manifest_sha256"] != _file_sha256(native_manifest_path)
        or not isinstance(report["origin"], str)
        or not report["origin"].strip()
    ):
        raise ValueError("RTL input report is not bound to the packaged native module")
    source_path = root / "source_inputs.json"
    provenance_path = root / "build_provenance.json"
    source = _json(source_path, 4 * 1024 * 1024)
    provenance = _json(provenance_path, 64 * 1024, allow_utf8_bom=True)
    if not isinstance(source, dict) or set(source) != {"schema", "source_sha256", "entries"}:
        raise ValueError("packaged source snapshot has an invalid contract")
    encoded_entries = json.dumps(source["entries"], sort_keys=True, separators=(",", ":")).encode()
    source_hash = hashlib.sha256(encoded_entries).hexdigest()
    if (
        source["schema"] != "sdr-source-snapshot-v1"
        or not isinstance(source["entries"], list)
        or source["source_sha256"] != source_hash
        or report.get("source_sha256") != source_hash
        or not isinstance(provenance, dict)
        or provenance.get("schema") != "sdr-pipeline-provenance-v1"
        or provenance.get("source_sha256") != source_hash
        or provenance.get("source_commit") != report["source_commit"]
        or provenance.get("native_sha256") != report["native_sha256"]
        or provenance.get("rtl_official_requested") is not True
        or provenance.get("rtl_source_commit") != report["source_commit"]
        or provenance.get("rtl_runtime_input_sha256") != report["input_manifest_sha256"]
        or report["source_commit"] != native_manifest.get("source_commit")
        or report["native_source_commit"] != native_manifest.get("source_commit")
    ):
        raise ValueError("RTL source/native/build provenance is inconsistent")
    runtime_hashes, notice_hashes = report["runtime_sha256"], report["notices_sha256"]
    if (
        not isinstance(runtime_hashes, dict)
        or not runtime_hashes
        or len(runtime_hashes) > MAX_FILES + 1
        or report.get("library") != "rtlsdr.dll"
        or "rtlsdr.dll" not in runtime_hashes
        or not isinstance(notice_hashes, dict)
        or not 1 <= len(notice_hashes) <= MAX_FILES
    ):
        raise ValueError("RTL runtime/report closure is absent")
    external_path = runtime_dir / "rtl_external_runtime.json"
    external_matches = tuple(path.resolve() for path in root.rglob("rtl_external_runtime.json") if path.is_file())
    if external_matches != (external_path.resolve(),) or external_path.is_symlink():
        raise ValueError("RTL runtime sidecar is missing, duplicated or misplaced")
    external = _json(external_path)
    expected_external = {
        "library": runtime_hashes["rtlsdr.dll"],
        "dependencies": {name: digest for name, digest in runtime_hashes.items() if name != "rtlsdr.dll"},
    }
    if external != expected_external:
        raise ValueError("RTL runtime sidecar differs from the admitted package report")
    observed_files = [path for path in root.rglob("*") if path.is_file()]
    import_map = report["imports"]
    if not isinstance(import_map, dict):
        raise ValueError("RTL import report is invalid")
    graph: dict[str, set[str]] = {}
    observed_imports: dict[str, list[str]] = {}
    for name, digest in runtime_hashes.items():
        if (
            not isinstance(name, str)
            or name != name.casefold()
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}\.dll", name, re.ASCII)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("RTL runtime report contains an invalid basename or hash")
        if PureWindowsPath(name).stem in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)),
        }:
            raise ValueError("RTL runtime report contains a reserved Windows device name")
        found = [path for path in observed_files if path.name.casefold() == name.casefold()]
        canonical = runtime_dir / name
        if len(found) != 1 or found[0] != canonical or _file_sha256(canonical) != digest:
            raise ValueError("RTL runtime DLL is absent, duplicated, misplaced or mutated: " + name)
        image_bytes = _bounded_file(canonical, MAX_DLL)
        imports, _ = _pe_image(canonical, image_bytes)
        observed_imports[name] = sorted(set(imports))
        graph[name] = set()
        for imported in imports:
            if _system_import(imported):
                if (runtime_dir / imported).exists():
                    raise ValueError("RTL system import is shadowed in frozen runtime: " + imported)
                continue
            if imported not in runtime_hashes:
                raise ValueError("frozen RTL dependency closure is incomplete: " + imported)
            graph[name].add(imported)
    reachable: set[str] = set()
    pending = list(graph.get("rtlsdr.dll", ()))
    while pending:
        imported = pending.pop()
        if imported in reachable:
            continue
        reachable.add(imported)
        pending.extend(graph.get(imported, ()))
    if observed_imports != import_map or reachable != set(runtime_hashes) - {"rtlsdr.dll"}:
        raise ValueError("frozen RTL import closure differs from the admitted report")
    for name, digest in notice_hashes.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(?:txt|md)", name, re.ASCII)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("RTL notice report contains an invalid basename or hash")
        path = runtime_dir / "rtl_notices" / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0 or path.stat().st_size > 1_048_576:
            raise ValueError("RTL notice is absent, unsafe or unbounded: " + name)
        if _file_sha256(path) != digest:
            raise ValueError("RTL notice hash differs: " + name)
        matches = tuple(candidate.resolve() for candidate in root.rglob(name) if candidate.is_file())
        if matches != (path.resolve(),):
            raise ValueError("RTL notice is duplicated or misplaced: " + name)
    notices_dir = runtime_dir / "rtl_notices"
    if {path.name.casefold() for path in notices_dir.iterdir() if path.is_file()} != {
        n.casefold() for n in notice_hashes
    }:
        raise ValueError("RTL notice directory contains unreported files")
    if report_path.resolve() not in {p.resolve() for p in observed_files}:
        raise ValueError("RTL input report is not part of frozen package")
    return {
        "schema": "app07-frozen-rtl-runtime-static-v1",
        "native_sha256": _file_sha256(native),
        "runtime_sha256": runtime_hashes,
        "notices_sha256": notice_hashes,
        "release_manifest_files": len(release["files"]),
        "sdk_loaded": False,
        "discovery_attempted": False,
        "rx_attempted": False,
        "scope": "static package integrity only; not ABI, redistribution, device or release qualification",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_frozen_rtl_runtime(args.package_dir, args.manifest, args.version), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    raise SystemExit(main())
