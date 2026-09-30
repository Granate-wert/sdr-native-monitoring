"""Paired optional geometry admission. No discovery, SDK initialization or RX."""

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path

from ..domain.sweep_capacity import (
    SWEEP_GEOMETRY_CONTRACT_VERSION, SWEEP_MAX_REDUCED_BYTES, SWEEP_MAX_SEGMENTS,
)


def sweep_geometry_contract(native: object, manifest: Mapping[str, object]) -> int | None:
    missing = object()
    values = (getattr(native, "SWEEP_GEOMETRY_CONTRACT_VERSION", missing),
              getattr(native, "SWEEP_MAX_SEGMENTS", missing),
              getattr(native, "SWEEP_MAX_REDUCED_BYTES", missing),
              manifest.get("sweep_geometry_contract_version", missing),
              manifest.get("sweep_max_segments", missing),
              manifest.get("sweep_max_reduced_bytes", missing))
    if all(value is missing for value in values):
        return None
    if (any(type(value) is not int for value in values)
            or values != (SWEEP_GEOMETRY_CONTRACT_VERSION, SWEEP_MAX_SEGMENTS, SWEEP_MAX_REDUCED_BYTES,
                          SWEEP_GEOMETRY_CONTRACT_VERSION, SWEEP_MAX_SEGMENTS, SWEEP_MAX_REDUCED_BYTES)):
        raise ValueError("Sweep geometry contract differs from its paired native manifest")
    return SWEEP_GEOMETRY_CONTRACT_VERSION


def require_extended_sweep_geometry(native: object) -> None:
    """Read only the loaded artifact's own sibling; never load an alternative."""
    path = getattr(native, "__file__", None)
    if not isinstance(path, str):
        raise ValueError("Extended Sweep requires a manifest-qualified native artifact")
    artifact = Path(path)
    try:
        with artifact.with_name("native_build_manifest.json").open("rb") as stream:
            encoded = stream.read(16_385)
        if len(encoded) > 16_384:
            raise ValueError("Sweep native manifest exceeds its bound")
        manifest = json.loads(encoded)
        if not isinstance(manifest, dict) or sweep_geometry_contract(native, manifest) != 1:
            raise ValueError("Extended Sweep geometry is unavailable")
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if manifest.get("artifact_sha256") != digest:
            raise ValueError("Sweep native artifact differs from its paired manifest")
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Extended Sweep native qualification is unavailable") from error
