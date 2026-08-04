"""Preflight and hash a standalone SDR distribution directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

FORBIDDEN_NAMES = ("esw_dfl", "_sgram_native", "dfl-analyzer")
FORBIDDEN_TEXT = ("esw_dfl", "_sgram_native")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(dist_dir: Path, lane: str, product_version: str) -> dict[str, object]:
    if not dist_dir.is_dir():
        raise ValueError(f"distribution directory does not exist: {dist_dir}")
    files: list[dict[str, object]] = []
    violations: list[str] = []
    for path in sorted(item for item in dist_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(dist_dir).as_posix()
        lowered = relative.casefold()
        if any(token in lowered for token in FORBIDDEN_NAMES):
            violations.append(relative)
        if path.suffix.casefold() in {".py", ".pyw", ".json", ".txt", ".md", ".xml", ".toml"}:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                content = ""
            for token in FORBIDDEN_TEXT:
                if token in content:
                    violations.append(f"{relative}: contains {token}")
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    exe = dist_dir / "SDRNativeMonitoring.exe"
    if not exe.is_file():
        raise ValueError("SDRNativeMonitoring.exe is missing")
    if violations:
        raise ValueError("forbidden standalone distribution content: " + "; ".join(sorted(set(violations))))
    return {"schema": "sdr-native-release-manifest", "schema_version": 1, "product": "SDR Native Monitoring", "version": product_version, "lane": lane, "python_abi": "cp313", "files": files}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--lane", choices=("CPU", "CUDA"), required=True)
    parser.add_argument("--version", default="0.16.10")
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(args.dist_dir.resolve(), args.lane, args.version)
    except (OSError, ValueError) as error:
        print(f"S12 preflight failed: {error}", file=sys.stderr)
        return 2
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    part = args.manifest.with_suffix(args.manifest.suffix + ".part")
    part.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    part.replace(args.manifest)
    print(json.dumps({"manifest": str(args.manifest), "files": len(manifest["files"]), "lane": args.lane}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
