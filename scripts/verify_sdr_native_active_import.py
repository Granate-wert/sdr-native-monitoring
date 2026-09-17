"""Verify canonical active native-module import in one isolated Python process.

This is a release-artifact identity check. It does not discover, configure,
open or stream an SDR device.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any


CANONICAL_MODULE_NAME = "sdr_monitor._sdr_native"
LEGACY_MODULE_NAME = "esw_dfl._sdr_native"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read active native manifest: {path}") from error
    if not isinstance(manifest, dict):
        raise ValueError("active native manifest must be a JSON object")
    digest = manifest.get("artifact_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("active native manifest has no valid artifact_sha256")
    return manifest


def verify_active_import(module_path: Path, manifest_path: Path) -> dict[str, object]:
    """Import the active module and legacy facade once, then assert one identity."""

    expected_module = module_path.resolve()
    manifest = _read_manifest(manifest_path.resolve())
    if not expected_module.is_file():
        raise ValueError("active native module is missing")
    if _file_sha256(expected_module) != manifest["artifact_sha256"]:
        raise ValueError("active native module does not match manifest artifact_sha256")

    repository = Path(__file__).resolve().parents[1]
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))

    standalone = importlib.import_module(CANONICAL_MODULE_NAME)
    loaded_path = Path(str(getattr(standalone, "__file__", ""))).resolve()
    if loaded_path != expected_module:
        raise ValueError("canonical import did not load the expected active module")
    if str(getattr(standalone, "__name__", "")) != CANONICAL_MODULE_NAME:
        raise ValueError("active native module has a non-canonical identity")

    from esw_dfl.sdr import native_api

    if native_api.NATIVE_MODULE_NAME != CANONICAL_MODULE_NAME:
        raise ValueError("legacy facade no longer targets the canonical module")
    if native_api.require_native() is not standalone:
        raise ValueError("legacy facade did not return the canonical native module")
    if LEGACY_MODULE_NAME in sys.modules:
        raise ValueError("legacy native-module identity was loaded")
    module_names = sorted(name for name, value in sys.modules.items() if value is standalone)
    if module_names != [CANONICAL_MODULE_NAME]:
        raise ValueError("canonical native module has unexpected alias identities")

    info = dict(standalone.build_info())
    if manifest.get("cuda_compiled") != bool(info.get("cuda_compiled")):
        raise ValueError("active native build_info does not match manifest lane")
    schema = dict(standalone.contract_schema())
    return {
        "module": str(loaded_path),
        "module_sha256": _file_sha256(loaded_path),
        "module_identity": standalone.__name__,
        "module_names": module_names,
        "schema": schema.get("schema"),
        "schema_version": schema.get("schema_version"),
        "cuda_compiled": bool(info.get("cuda_compiled")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify_active_import(args.module, args.manifest)
    except (ImportError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
