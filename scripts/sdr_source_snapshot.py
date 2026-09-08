"""Deterministic source inventory, including relevant untracked working files.

This identifies source bytes, not the compiler output: callers must capture
before building and compare after building, then bind the artifact separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TREES = ("sdr_monitor", "native/sdr_core", "scripts")
EXCLUDED_DIRS = {"out", "build", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SUFFIXES = {".py", ".cpp", ".hpp", ".h", ".c", ".cu", ".cuh", ".cmake", ".toml", ".json", ".txt"}
ENTRY_FILES = ("main_sdr.py", "pyproject.toml", "build_native_sdr.ps1", "build_sdr_release.ps1")


def snapshot(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    paths = set()
    for tree in TREES:
        directory = root / tree
        if not directory.is_dir():
            raise ValueError(f"required source tree missing: {tree}")
        for path in directory.rglob("*"):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_DIRS for part in relative.parts):
                continue
            if path.is_file() and path.suffix.lower() in SUFFIXES:
                if path.name == "native_build_manifest.json":
                    continue
                if not path.resolve().is_relative_to(root):
                    raise ValueError(f"source escapes workspace: {relative}")
                paths.add(relative.as_posix())
    for name in ENTRY_FILES:
        if not (root / name).is_file():
            raise ValueError(f"required build input missing: {name}")
        paths.add(name)
    entries = [{"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
               for name in sorted(paths)]
    serialized = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": "sdr-source-snapshot-v1", "entries": entries,
            "source_sha256": hashlib.sha256(serialized).hexdigest()}


def verify(root: Path, recorded: dict[str, object]) -> None:
    if snapshot(root) != recorded:
        raise ValueError("source inputs changed or snapshot is invalid; rebuild required")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if bool(args.output) == bool(args.verify):
        parser.error("choose exactly one of --output or --verify")
    if args.verify:
        verify(args.root, json.loads(args.verify.read_text(encoding="utf-8")))
        print("source snapshot unchanged")
    else:
        value = snapshot(args.root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        print(value["source_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
