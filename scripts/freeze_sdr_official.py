"""Generate a pinned standard spec with one checked shared-DLL payload.

Only generated build artifacts are written. No SDK init, active-native copy,
system DLL replacement or post-freeze deletion/substitution is involved.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.preflight_sdr_native_build import _read_manifest
from scripts.preflight_sdr_shared_runtime import validate_shared_runtime
from sdr_monitor.frozen_shared_runtime import SHARED_COMPONENTS
from sdr_monitor.libiio_runtime import LIBIIO_RUNTIME_COMPONENTS


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


def freeze(root: Path, native_directory: Path, libiio_directory: Path, release_root: Path, build_root: Path) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("repo-root", "native-directory", "libiio-directory", "release-root", "build-root"):
        parser.add_argument("--" + argument, type=Path, required=True)
    args = parser.parse_args()
    freeze(args.repo_root, args.native_directory, args.libiio_directory, args.release_root, args.build_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
