#!/usr/bin/env python3
"""Generate the local, deterministic UI2-00 protected-backend manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

try:  # Direct script execution keeps only tests/ui_v2 on sys.path.
    from tests.ui_v2.ui2_scope import PROTECTED_PREFIXES
except ModuleNotFoundError:  # pragma: no cover - exercised by the CLI itself.
    from ui2_scope import PROTECTED_PREFIXES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True
    ).stdout.strip()


def build_manifest(repository: Path) -> dict[str, object]:
    records: list[dict[str, str]] = []
    for root in PROTECTED_PREFIXES:
        directory = repository / root.rstrip("/")
        if not directory.exists():
            continue
        for candidate in sorted(path for path in directory.rglob("*") if path.is_file()):
            records.append(
                {
                    "path": candidate.relative_to(repository).as_posix(),
                    "sha256": _sha256(candidate),
                }
            )
    return {
        "schema_version": 1,
        "base_commit": _head(),
        "protected_roots": list(PROTECTED_PREFIXES),
        "files": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    repository = Path.cwd()
    manifest = build_manifest(repository)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest['files'])} protected-file hashes to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
