"""Generate a pinned standard spec with one checked shared-DLL payload.

Only generated build artifacts are written. No SDK init, active-native copy,
system DLL replacement or post-freeze deletion/substitution is involved.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.preflight_sdr_native_build import _read_manifest
from scripts.preflight_sdr_freezer import verify_freezer_version
from scripts.preflight_sdr_shared_runtime import validate_shared_runtime
from sdr_monitor.frozen_shared_runtime import SHARED_COMPONENTS
from sdr_monitor.libiio_runtime import LIBIIO_RUNTIME_COMPONENTS

MAX_RTL_METADATA = 16 * 1024


def _read_rtl_metadata(path: Path) -> dict[str, object]:
    """Read bounded strict-UTF-8 JSON emitted by the RTL admission step."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("RTL build metadata is missing or unsafe: " + path.name)
    with path.open("rb") as stream:
        data = stream.read(MAX_RTL_METADATA + 1)
    if len(data) > MAX_RTL_METADATA:
        raise ValueError("RTL build metadata exceeds its size bound: " + path.name)

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate RTL build metadata key: " + key)
            result[key] = value
        return result

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("RTL build metadata must be strict UTF-8 JSON: " + path.name) from error
    if not isinstance(value, dict):
        raise ValueError("RTL build metadata must be a JSON object: " + path.name)
    return value


def checked_spec_source(source: str, root: Path, selected: dict[str, Path]) -> str:
    """Inject the checked TOC operation before PYZ; fail on generator drift."""
    tree = ast.parse(source)
    exes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "EXE"]
    pyz = [node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
           and isinstance(node.value.func, ast.Name) and node.value.func.id == "PYZ"]
    if len(exes) != 1 or len(pyz) != 1:
        raise ValueError("official freezer spec must contain one EXE and PYZ")
    keywords = {item.arg: item.value for item in exes[0].keywords}
    for key, expected in (("console", True), ("hide_console", "hide-early")):
        value = keywords.get(key)
        if not isinstance(value, ast.Constant) or type(value.value) is not type(expected) or value.value != expected:
            raise ValueError("official freezer spec changed console policy: " + key)
    if set(selected) != set(SHARED_COMPONENTS):
        raise ValueError("official freezer spec needs exactly nine selected dependencies")
    literals = ", ".join(repr(name) + ": Path(" + repr(str(path)) + ")" for name, path in sorted(selected.items()))
    lines = source.splitlines(keepends=True)
    lines.insert(pyz[0].lineno - 1, "a.binaries = deduplicate_shared_runtime_binaries(a.binaries, {" + literals + "})\n")
    prefix = ("import sys\nfrom pathlib import Path\n"
              + "sys.path.insert(0, " + repr(str(root)) + ")\n"
              + "from scripts.sdr_frozen_runtime_payload import deduplicate_shared_runtime_binaries\n")
    modified = prefix + "".join(lines)
    ast.parse(modified)
    return modified


def checked_rtl_spec_source(
    source: str,
    root: Path,
    selected: dict[str, Path],
    notices: dict[str, Path],
    hackrf_selected: dict[str, Path] | None = None,
) -> str:
    """Inject a distinct RTL-aware TOC guard without changing the HF guard."""
    tree = ast.parse(source)
    exes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EXE"
    ]
    pyz = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "PYZ"
    ]
    if len(exes) != 1 or len(pyz) != 1:
        raise ValueError("RTL freezer spec must contain one EXE and PYZ")
    keywords = {item.arg: item.value for item in exes[0].keywords}
    for key, expected in (("console", True), ("hide_console", "hide-early")):
        value = keywords.get(key)
        if not isinstance(value, ast.Constant) or type(value.value) is not type(expected) or value.value != expected:
            raise ValueError("RTL freezer spec changed console policy: " + key)

    def literal(mapping: dict[str, Path]) -> str:
        return (
            "{"
            + ", ".join(repr(name) + ": Path(" + repr(str(path)) + ")" for name, path in sorted(mapping.items()))
            + "}"
        )

    lines = source.splitlines(keepends=True)
    statements = ""
    imports = "from scripts.sdr_frozen_runtime_payload import deduplicate_rtl_runtime_binaries\n"
    if hackrf_selected is not None:
        if set(hackrf_selected) != set(SHARED_COMPONENTS):
            raise ValueError("combined RTL/HackRF spec needs exactly nine shared-runtime inputs")
        statements += "a.binaries = deduplicate_shared_runtime_binaries(a.binaries, " + literal(hackrf_selected) + ")\n"
        imports += "from scripts.sdr_frozen_runtime_payload import deduplicate_shared_runtime_binaries\n"
    statements += (
        "a.binaries = deduplicate_rtl_runtime_binaries(a.binaries, "
        + literal(selected)
        + ", {})\n"
        + "a.datas = deduplicate_rtl_runtime_binaries(a.datas, {}, "
        + literal(notices)
        + ")\n"
    )
    lines.insert(pyz[0].lineno - 1, statements)
    prefix = (
        "import sys\nfrom pathlib import Path\n"
        + "sys.path.insert(0, "
        + repr(str(root))
        + ")\n"
        + imports
    )
    modified = prefix + "".join(lines)
    ast.parse(modified)
    return modified


def freeze(root: Path, native_directory: Path, libiio_directory: Path, release_root: Path, build_root: Path) -> None:
    verify_freezer_version()
    from PyInstaller.building import makespec
    from PyInstaller.utils.cliutils.makespec import generate_parser

    root = root.resolve(strict=True)
    native_directory = native_directory.resolve(strict=True)
    libiio_directory = libiio_directory.resolve(strict=True)
    modules = tuple(native_directory.glob("_sdr_native*.pyd"))
    if len(modules) != 1:
        raise ValueError("official freeze requires one staged extension")
    manifest_path = native_directory / "native_build_manifest.json"
    manifest = _read_manifest(manifest_path)
    validate_shared_runtime(modules[0], manifest, libiio_directory, expected_cuda=False)
    if manifest.get("hackrf_official_compiled") is not True:
        raise ValueError("official freeze refuses a baseline module")
    selected = {name: libiio_directory / name for name in LIBIIO_RUNTIME_COMPONENTS}
    selected.update({name: native_directory / name for name in ("hackrf.dll", "pthreadVC3.dll")})
    arguments = ["--onedir", "--hide-console", "hide-early", "--name", "SDRNativeMonitoring",
                 "--specpath", str(build_root), "--hidden-import", "sdr_monitor.main",
                 "--add-binary", str(modules[0]) + ";sdr_monitor",
                 "--add-data", str(manifest_path) + ";sdr_monitor"]
    for name in ("esw_dfl", "olefile", "_sgram_native", "sdr_monitor._sdr_native"):
        arguments.extend(("--exclude-module", name))
    for path in selected.values():
        arguments.extend(("--add-binary", str(path) + ";sdr_monitor"))
    arguments.append(str(root / "main_sdr.py"))
    options = generate_parser().parse_args(arguments)
    spec = Path(makespec.main(options.scriptname, **vars(options)))
    spec.write_text(checked_spec_source(spec.read_text(encoding="utf-8"), root, selected), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(release_root),
                    "--workpath", str(build_root), str(spec)], cwd=root, check=True)


def freeze_rtl(
    root: Path,
    native_directory: Path,
    libiio_directory: Path,
    release_root: Path,
    build_root: Path,
    runtime_directory: Path,
    input_manifest: Path,
    admission_report: Path,
    external_manifest: Path,
    source_snapshot: Path,
    *,
    include_hackrf: bool,
) -> None:
    """Freeze RTL-only or HF+RTL using a separate closed-set TOC check."""
    from scripts.preflight_sdr_native_build import (
        _load_native_module,
        _file_sha256,
        validate_manifest,
        validate_rtl_factory,
    )
    from scripts.preflight_sdr_rtl_runtime import validate_rtl_inputs

    root, native_directory, libiio_directory, runtime_directory = (
        path.resolve(strict=True) for path in (root, native_directory, libiio_directory, runtime_directory)
    )
    if any(
        ";" in str(path) or "\x00" in str(path)
        for path in (root, native_directory, libiio_directory, runtime_directory, release_root, build_root)
    ):
        raise ValueError("RTL freezer paths cannot contain delimiter or NUL characters")
    modules = tuple(native_directory.glob("_sdr_native*.pyd"))
    if len(modules) != 1:
        raise ValueError("RTL freeze requires one staged extension")
    module = modules[0].resolve(strict=True)
    native_manifest_path = native_directory / "native_build_manifest.json"
    native_manifest = _read_manifest(native_manifest_path)
    validate_manifest(module, native_manifest, expected_cuda=False)
    validate_rtl_factory(_load_native_module(module), native_manifest)
    if native_manifest.get("rtl_official_compiled") is not True:
        raise ValueError("RTL freezer refuses a non-RTL module")
    admission = validate_rtl_inputs(
        module, native_manifest_path, runtime_directory, input_manifest, libiio_directory, source_snapshot
    )
    recorded = _read_rtl_metadata(admission_report)
    public_admission = {key: value for key, value in admission.items() if not key.startswith("_")}
    if recorded != public_admission:
        raise ValueError("RTL runtime inputs changed after preflight")
    external = _read_rtl_metadata(external_manifest)
    expected_external = {
        "library": admission["runtime_sha256"]["rtlsdr.dll"],
        "dependencies": {name: digest for name, digest in admission["runtime_sha256"].items() if name != "rtlsdr.dll"},
    }
    if external != expected_external:
        raise ValueError("RTL external-runtime sidecar differs from preflight")
    verify_freezer_version()
    from PyInstaller.building import makespec
    from PyInstaller.utils.cliutils.makespec import generate_parser
    hackrf_selected: dict[str, Path] | None = None
    selected: dict[str, Path] = {name: libiio_directory / name for name in LIBIIO_RUNTIME_COMPONENTS}
    if include_hackrf:
        validate_shared_runtime(module, native_manifest, libiio_directory, expected_cuda=False)
        if native_manifest.get("hackrf_official_compiled") is not True:
            raise ValueError("combined package requires the official HackRF module")
        hackrf_selected = dict(selected)
        hackrf_selected.update({name: native_directory / name for name in ("hackrf.dll", "pthreadVC3.dll")})
        if set(hackrf_selected) != set(SHARED_COMPONENTS):
            raise ValueError("combined package does not have the exact shared-runtime input set")
        selected = dict(hackrf_selected)
    for name, path in admission["_runtime_files"].items():
        prior = selected.get(name)
        if prior is not None and _file_sha256(prior) != _file_sha256(path):
            raise ValueError("RTL runtime conflicts with a selected package DLL: " + name)
        if prior is None:
            selected[name] = path
    notices = admission["_notice_files"]
    arguments = [
        "--onedir",
        "--hide-console",
        "hide-early",
        "--name",
        "SDRNativeMonitoring",
        "--specpath",
        str(build_root),
        "--hidden-import",
        "sdr_monitor.main",
        "--add-binary",
        str(module) + ";sdr_monitor",
        "--add-data",
        str(native_manifest_path) + ";sdr_monitor",
        "--add-data",
        str(external_manifest.resolve(strict=True)) + ";sdr_monitor",
    ]
    for name in ("esw_dfl", "olefile", "_sgram_native", "sdr_monitor._sdr_native"):
        arguments.extend(("--exclude-module", name))
    for path in selected.values():
        arguments.extend(("--add-binary", str(path) + ";sdr_monitor"))
    for path in notices.values():
        arguments.extend(("--add-data", str(path) + ";sdr_monitor/rtl_notices"))
    arguments.append(str(root / "main_sdr.py"))
    options = generate_parser().parse_args(arguments)
    spec = Path(makespec.main(options.scriptname, **vars(options)))
    spec.write_text(
        checked_rtl_spec_source(
            spec.read_text(encoding="utf-8"), root, selected, notices, hackrf_selected
        ), encoding="utf-8"
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(release_root),
            "--workpath",
            str(build_root),
            str(spec),
        ],
        cwd=root,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("repo-root", "native-directory", "libiio-directory", "release-root", "build-root"):
        parser.add_argument("--" + argument, type=Path, required=True)
    for argument in (
        "rtl-runtime-directory", "rtl-input-manifest", "rtl-admission-report", "rtl-external-manifest",
        "source-snapshot",
    ):
        parser.add_argument("--" + argument, type=Path)
    parser.add_argument("--include-hackrf", action="store_true")
    args = parser.parse_args()
    rtl_inputs = (
        args.rtl_runtime_directory,
        args.rtl_input_manifest,
        args.rtl_admission_report,
        args.rtl_external_manifest,
        args.source_snapshot,
    )
    if any(value is not None for value in rtl_inputs):
        if not all(value is not None for value in rtl_inputs) or args.source_snapshot is None:
            parser.error("all RTL freezer inputs are required together")
        freeze_rtl(
            args.repo_root,
            args.native_directory,
            args.libiio_directory,
            args.release_root,
            args.build_root,
            args.rtl_runtime_directory,
            args.rtl_input_manifest,
            args.rtl_admission_report,
            args.rtl_external_manifest,
            args.source_snapshot,
            include_hackrf=args.include_hackrf,
        )
    else:
        if args.include_hackrf:
            parser.error("--include-hackrf requires explicit RTL freezer inputs")
        freeze(args.repo_root, args.native_directory, args.libiio_directory, args.release_root, args.build_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
