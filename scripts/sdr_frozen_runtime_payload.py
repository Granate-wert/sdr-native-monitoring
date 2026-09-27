"""Normalize generated freezer TOC, never delete files or choose another SDK."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from scripts.preflight_sdr_native_build import _file_sha256
from sdr_monitor.frozen_shared_runtime import SHARED_COMPONENTS


def deduplicate_shared_runtime_binaries(
    entries: Sequence[tuple[str, str, str]], selected: Mapping[str, Path],
) -> list[tuple[str, str, str]]:
    """Retain the explicit sibling destination after checking ALL duplicates.

    PyInstaller's PE dependency analysis adds basename-root copies of DLLs
    already explicitly added in sdr_monitor. Only byte-identical BINARY entries
    may be omitted. A different payload or missing canonical entry refuses the
    build. Unrelated DLLs and the native extension are not touched.
    """
    if set(selected) != set(SHARED_COMPONENTS):
        raise ValueError("shared freezer selection must contain exactly nine dependencies")
    hashes = {name.casefold(): _file_sha256(path) for name, path in selected.items()}
    destinations = {name.casefold(): "sdr_monitor/" + name for name in selected}
    seen: set[str] = set()
    result: list[tuple[str, str, str]] = []
    for destination, source, kind in entries:
        normalized = destination.replace("\\", "/")
        name = PurePosixPath(normalized).name.casefold()
        if name not in hashes:
            result.append((destination, source, kind))
            continue
        if kind != "BINARY" or _file_sha256(Path(source)) != hashes[name]:
            raise ValueError("freezer shared dependency has incompatible bytes/type: " + name)
        if normalized.casefold() == destinations[name].casefold():
            if name in seen:
                raise ValueError("freezer repeated canonical shared dependency: " + name)
            seen.add(name)
            result.append((destinations[name], source, kind))
        # A root/other-destination copy was checked before omission.
    if seen != set(hashes):
        raise ValueError("freezer lost an explicit canonical shared dependency")
    return result
