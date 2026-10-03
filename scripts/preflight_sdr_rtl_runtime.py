"""Admit a hash-bound external RTL DLL set without loading the vendor SDK.

This is a static packaging check only. It neither establishes redistribution
rights nor validates the vendor ABI or a physical receiver.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.preflight_sdr_native_build import (
    _file_sha256,
    _read_manifest,
    _load_native_module,
    validate_manifest,
    validate_rtl_factory,
)
from scripts.sdr_source_snapshot import snapshot

MAX_DLL = 64 * 1024 * 1024
MAX_JSON = 16 * 1024
MAX_NOTICE = 1024 * 1024
MAX_FILES = 8
SYSTEM_IMPORTS = frozenset(
    (
        "kernel32.dll",
        "kernelbase.dll",
        "advapi32.dll",
        "user32.dll",
        "bcrypt.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleaut32.dll",
        "ws2_32.dll",
        "setupapi.dll",
        "cfgmgr32.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "ucrtbase.dll",
        "msvcrt.dll",
    )
)
REQUIRED_EXPORTS = frozenset(
    (
        "rtlsdr_get_device_count",
        "rtlsdr_get_device_usb_strings",
        "rtlsdr_open",
        "rtlsdr_close",
        "rtlsdr_get_usb_strings",
        "rtlsdr_get_tuner_type",
        "rtlsdr_get_direct_sampling",
        "rtlsdr_get_offset_tuning",
        "rtlsdr_set_sample_rate",
        "rtlsdr_get_sample_rate",
        "rtlsdr_set_center_freq",
        "rtlsdr_get_center_freq",
        "rtlsdr_set_tuner_gain_mode",
        "rtlsdr_reset_buffer",
        "rtlsdr_read_async",
        "rtlsdr_cancel_async",
    )
)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.dll$", re.ASCII)
_HASH = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


def _sha(path: Path) -> str:
    return _file_sha256(path)


def _json_no_duplicates(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("runtime manifest must be strict UTF-8 JSON") from error


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("runtime input must be a regular non-symlink file: " + path.name)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("runtime input exceeds its size bound: " + path.name)
    return data


def _safe_sibling(directory: Path, name: str, limit: int) -> tuple[Path, bytes]:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        or name in (".", "..")
    ):
        raise ValueError("runtime input name is not a simple sibling")
    path = directory / name
    stem = PureWindowsPath(name).stem.casefold()
    if stem in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        raise ValueError("runtime input uses a reserved Windows device name")
    data = _read_bounded(path, limit)
    try:
        if path.resolve(strict=True).parent != directory.resolve(strict=True):
            raise ValueError("runtime input escaped its selected directory")
    except OSError as error:
        raise ValueError("runtime input cannot be resolved") from error
    return path, data


def _pe_image(path: Path, data: bytes) -> tuple[list[str], set[str]]:
    """Read bounded x64 PE imports/exports (RVA translation; no module load)."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("RTL runtime is not a bounded PE image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if pe > len(data) - 24 or data[pe : pe + 4] != b"PE\0\0":
        raise ValueError("RTL runtime PE header is invalid")
    machine, section_count, _, _, _, opt_size, characteristics = struct.unpack_from("<HHIIIHH", data, pe + 4)
    opt = pe + 24
    if (machine != 0x8664 or characteristics & 0x2000 == 0 or section_count < 1
            or section_count > 96 or opt_size < 112 or opt + opt_size > len(data)):
        raise ValueError("RTL runtime must be bounded x64 PE32+")
    if struct.unpack_from("<H", data, opt)[0] != 0x20B:
        raise ValueError("RTL runtime must be PE32+")
    directory_count = struct.unpack_from("<I", data, opt + 108)[0]
    if directory_count <= 13 or opt_size < 112 + 14 * 8:
        raise ValueError("RTL runtime PE directories are incomplete")
    import_rva, import_size = struct.unpack_from("<II", data, opt + 112 + 8)
    delay_rva, delay_size = struct.unpack_from("<II", data, opt + 112 + 13 * 8)
    if delay_rva or delay_size:
        raise ValueError("RTL runtime delay imports are unsupported")
    export_rva, export_size = struct.unpack_from("<II", data, opt + 112)
    sections_at = opt + opt_size
    if sections_at + section_count * 40 > len(data):
        raise ValueError("RTL runtime sections exceed image bounds")
    sections = [struct.unpack_from("<8sIIIIIIHHI", data, sections_at + i * 40) for i in range(section_count)]

    def rva(value: int, count: int = 1) -> int:
        if value < 0 or count < 0:
            raise ValueError("invalid PE RVA")
        for section in sections:
            _, virtual_size, virtual_address, raw_size, raw_ptr, *_ = section
            span = raw_size
            if value >= virtual_address and value + count <= virtual_address + span:
                offset = raw_ptr + value - virtual_address
                if offset <= len(data) and count <= len(data) - offset:
                    return offset
        raise ValueError("RTL runtime PE RVA is outside bounded raw sections")

    def cstring(value: int, maximum: int = 128, *, fold: bool = False) -> str:
        offset = rva(value)
        end = data.find(b"\0", offset, min(len(data), offset + maximum + 1))
        if end < 0 or end == offset:
            raise ValueError("RTL runtime PE name is missing or unbounded")
        try:
            text = data[offset:end].decode("ascii")
            return text.lower() if fold else text
        except UnicodeDecodeError as error:
            raise ValueError("RTL runtime PE names must be ASCII") from error

    imports: list[str] = []
    if import_rva or import_size:
        if not import_rva or import_size < 20 or import_size > 65_536:
            raise ValueError("RTL runtime PE import directory is unbounded")
        for index in range(import_size // 20):
            desc = rva(import_rva + index * 20, 20)
            name_rva = struct.unpack_from("<I", data, desc + 12)[0]
            if not name_rva:
                break
            imports.append(cstring(name_rva, fold=True))
        else:
            raise ValueError("RTL runtime PE import table lacks a terminator")
    exports: set[str] = set()
    if export_rva and export_size:
        if export_size > MAX_DLL:
            raise ValueError("RTL runtime PE export directory exceeds bound")
        rva(export_rva, export_size)
        exp = rva(export_rva, 40)
        export_fields = struct.unpack_from("<IIHHIIIIIII", data, exp)
        function_count, name_count = export_fields[6], export_fields[7]
        functions_rva, names_rva = export_fields[8], export_fields[9]
        if not function_count or name_count > 65_536:
            raise ValueError("RTL runtime PE export table exceeds bound")
        if function_count > 65_536:
            raise ValueError("RTL runtime PE function table exceeds bound")
        functions_at = rva(functions_rva, function_count * 4)
        ordinals_rva = export_fields[10]
        ordinals_at = rva(ordinals_rva, name_count * 2)
        names_at = rva(names_rva, name_count * 4)
        for i in range(function_count):
            target = struct.unpack_from("<I", data, functions_at + i * 4)[0]
            if export_rva <= target < export_rva + export_size:
                raise ValueError("RTL runtime PE forwarded exports are unsupported")
        for i in range(name_count):
            name = cstring(struct.unpack_from("<I", data, names_at + i * 4)[0], 256)
            ordinal = struct.unpack_from("<H", data, ordinals_at + i * 2)[0]
            if ordinal >= function_count:
                raise ValueError("RTL runtime PE export ordinal is outside function table")
            target = struct.unpack_from("<I", data, functions_at + ordinal * 4)[0]
            if name in REQUIRED_EXPORTS:
                if not target:
                    raise ValueError("RTL runtime required export has no function target")
                rva(target)
            exports.add(name)
    if path.name.casefold() == "rtlsdr.dll" and not REQUIRED_EXPORTS.issubset(exports):
        raise ValueError("RTL DLL is missing required control exports")
    return imports, exports


def _system_import(name: str) -> bool:
    return name in SYSTEM_IMPORTS or name.startswith("api-ms-win-") or name.startswith("ext-ms-win-")


def _validate_source_snapshot(path: Path, repo_root: Path) -> tuple[str, str]:
    recorded = _json_no_duplicates(_read_bounded(path, 4 * 1024 * 1024))
    if not isinstance(recorded, dict) or set(recorded) != {"schema", "source_sha256", "entries"}:
        raise ValueError("source snapshot has an unsupported shape")
    entries = recorded["entries"]
    if recorded["schema"] != "sdr-source-snapshot-v1" or not isinstance(entries, list):
        raise ValueError("source snapshot schema is unsupported")
    encoded_entries = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    source_hash = hashlib.sha256(encoded_entries).hexdigest()
    if recorded["source_sha256"] != source_hash or snapshot(repo_root) != recorded:
        raise ValueError("source snapshot does not match current source bytes")
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit):
        raise ValueError("current source commit is not a canonical Git object ID")
    return source_hash, source_commit


def validate_rtl_inputs(
    module: Path,
    native_manifest_path: Path,
    runtime_directory: Path,
    input_manifest_path: Path,
    libiio_directory: Path,
    source_snapshot_path: Path,
) -> dict[str, Any]:
    if runtime_directory.is_symlink() or input_manifest_path.is_symlink():
        raise ValueError("RTL runtime directory and input manifest may not be symlinks")
    module, runtime_directory, libiio_directory = (
        p.resolve(strict=True) for p in (module, runtime_directory, libiio_directory)
    )
    native_manifest = _read_manifest(native_manifest_path.resolve(strict=True))
    validate_manifest(module, native_manifest, expected_cuda=False)
    validate_rtl_factory(_load_native_module(module), native_manifest)
    source_hash, source_commit = _validate_source_snapshot(
        source_snapshot_path, Path(__file__).resolve().parents[1]
    )
    if (
        native_manifest.get("rtl_official_compiled") is not True
        or native_manifest.get("rtl_control_contract_version") != 1
    ):
        raise ValueError("RTL package requires the exact official CPU control-contract-1 module")
    if native_manifest.get("source_commit") != source_commit:
        raise ValueError("RTL native artifact source commit differs from current source")
    manifest_bytes = _read_bounded(input_manifest_path, MAX_JSON)
    spec = _json_no_duplicates(manifest_bytes)
    if not isinstance(spec, dict) or set(spec) != {"schema", "library", "dependencies", "origin", "notices"}:
        raise ValueError("RTL input manifest has an unsupported shape")
    if spec["schema"] != "app07-rtl-runtime-input-v1":
        raise ValueError("RTL input manifest schema is unsupported")
    origin = spec["origin"]
    if not isinstance(origin, str) or not origin.strip() or len(origin) > 512 or "\x00" in origin:
        raise ValueError("RTL origin must be bounded nonempty provenance text")
    deps, notices = spec["dependencies"], spec["notices"]
    if (
        not isinstance(deps, dict)
        or len(deps) > MAX_FILES
        or not isinstance(notices, dict)
        or not 1 <= len(notices) <= MAX_FILES
    ):
        raise ValueError("RTL dependency/notices collection is outside its bound")
    entries: dict[str, tuple[Path, str, bytes]] = {}
    for name, digest, limit in [("rtlsdr.dll", spec["library"], MAX_DLL), *[(k, v, MAX_DLL) for k, v in deps.items()]]:
        if (
            not isinstance(name, str)
            or not _NAME.fullmatch(name)
            or not isinstance(digest, str)
            or not _HASH.fullmatch(digest)
        ):
            raise ValueError("RTL DLL names/hashes must be exact ASCII basenames and lowercase SHA-256")
        key = name.casefold()
        if key in entries or (key in {n.casefold() for n in deps} and name != key):
            raise ValueError("RTL runtime DLL has a duplicate/case alias")
        path, data = _safe_sibling(runtime_directory, name, limit)
        observed = hashlib.sha256(data).hexdigest()
        if observed != digest:
            raise ValueError("RTL runtime DLL hash mismatch: " + name)
        entries[key] = (path, digest, data)
    if "rtlsdr.dll" not in entries:
        raise ValueError("RTL primary DLL must be named rtlsdr.dll")
    notice_files: dict[str, tuple[Path, str, bytes]] = {}
    for name, digest in notices.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(?:txt|md)", name, re.ASCII)
            or not isinstance(digest, str)
            or not _HASH.fullmatch(digest)
        ):
            raise ValueError("RTL notices need simple ASCII TXT/MD names and SHA-256")
        key = name.casefold()
        if key in notice_files:
            raise ValueError("RTL notice names contain a case alias")
        path, data = _safe_sibling(runtime_directory, name, MAX_NOTICE)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("RTL notice must be UTF-8 text: " + name) from error
        if not text.strip() or "\x00" in text or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("RTL notice is empty or its hash differs: " + name)
        notice_files[key] = (path, digest, data)
    imports_by_image: dict[str, list[str]] = {}
    graph: dict[str, set[str]] = {}
    for key, (path, _, data) in entries.items():
        imports, _ = _pe_image(path, data)
        imports_by_image[path.name] = sorted(set(imports))
        graph[key] = set()
        for imported in imports:
            if _system_import(imported):
                if (runtime_directory / imported).exists():
                    raise ValueError("RTL system import is shadowed by a local file: " + imported)
                continue
            if imported not in entries:
                raise ValueError("RTL import is not declared in the hash-admitted closure: " + imported)
            graph[key].add(imported)
    reachable: set[str] = set()
    pending = list(graph["rtlsdr.dll"])
    while pending:
        image = pending.pop()
        if image in reachable:
            continue
        reachable.add(image)
        pending.extend(graph.get(image, ()))
    if reachable != set(entries) - {"rtlsdr.dll"}:
        raise ValueError("RTL dependency set contains unused or unresolved DLLs")
    selected_libusb = libiio_directory / "libusb-1.0.dll"
    if not selected_libusb.is_file():
        raise ValueError("selected shared libusb runtime is missing")
    rtl_usb = entries.get("libusb-1.0.dll")
    if rtl_usb is not None and _sha(selected_libusb) != rtl_usb[1]:
        raise ValueError("RTL/libiio shared libusb collision has different bytes")
    return {
        "schema": "app07-rtl-runtime-input-v1",
        "passed": True,
        "source_commit": source_commit,
        "source_sha256": source_hash,
        "native_sha256": _sha(module),
        "native_source_commit": native_manifest.get("source_commit"),
        "native_manifest_sha256": _sha(native_manifest_path.resolve(strict=True)),
        "input_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "origin": origin,
        "library": "rtlsdr.dll",
        "runtime_sha256": {p.name: digest for p, digest, _ in entries.values()},
        "notices_sha256": {p.name: digest for p, digest, _ in notice_files.values()},
        "imports": imports_by_image,
        "scope": "static input admission only; no ABI, RX, SDK load or license approval",
        "_runtime_files": {p.name: p for p, _, _ in entries.values()},
        "_notice_files": {p.name: p for p, _, _ in notice_files.values()},
        "_dependencies": {p.name: digest for key, (p, digest, _) in entries.items() if key != "rtlsdr.dll"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in (
        "module",
        "native-manifest",
        "runtime-directory",
        "runtime-manifest",
        "libiio-directory",
        "source-snapshot",
        "output-directory",
    ):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_rtl_inputs(args.module, args.native_manifest, args.runtime_directory,
                                     args.runtime_manifest, args.libiio_directory, args.source_snapshot)
        output = args.output_directory
        report_path, runtime_path = output / "rtl_runtime_inputs.json", output / "rtl_external_runtime.json"
        if report_path.exists() or runtime_path.exists():
            raise ValueError("RTL build-only admission outputs already exist; refusing overwrite")
        output.mkdir(parents=True, exist_ok=True)
        report = {k: v for k, v in result.items() if not k.startswith("_")}
        external = {"library": result["runtime_sha256"]["rtlsdr.dll"], "dependencies": result["_dependencies"]}
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        runtime_path.write_text(json.dumps(external, indent=2), encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"RTL runtime preflight failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
