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


def deduplicate_rtl_runtime_binaries(
    entries: Sequence[tuple[str, str, str]],
    selected: Mapping[str, Path],
    notices: Mapping[str, Path] | None = None,
) -> list[tuple[str, str, str]]:
    """Check every duplicate RTL DLL/notice before retaining its one canonical copy.

    This separate bounded path intentionally does not alter the established
    nine-component HF/Pluto policy above. DLLs live beside the native module;
    vendor notices live in the dedicated rtl_notices data directory.
    """
    expected: dict[str, tuple[str, Path, str]] = {}
    for name, path in selected.items():
        key = name.casefold()
        if key in expected or Path(name).name != name or not name.lower().endswith(".dll"):
            raise ValueError("RTL freezer selection has duplicate or invalid DLL names")
        expected[key] = ("sdr_monitor/" + name, path, "BINARY")
    for name, path in (notices or {}).items():
        key = name.casefold()
        if key in expected or Path(name).name != name or Path(name).suffix.lower() not in {".txt", ".md"}:
            raise ValueError("RTL notice selection has duplicate or invalid names")
        expected[key] = ("sdr_monitor/rtl_notices/" + name, path, "DATA")
    hashes = {key: _file_sha256(source) for key, (_, source, _) in expected.items()}
    canonical = {key: destination.casefold() for key, (destination, _, _) in expected.items()}
    seen: set[str] = set()
    result: list[tuple[str, str, str]] = []
    for destination, source, kind in entries:
        normalized = destination.replace("\\", "/")
        key = PurePosixPath(normalized).name.casefold()
        if key not in expected:
            result.append((destination, source, kind))
            continue
        expected_destination, _, expected_kind = expected[key]
        if kind != expected_kind or _file_sha256(Path(source)) != hashes[key]:
            raise ValueError("freezer RTL payload has incompatible bytes/type: " + key)
        if normalized.casefold() == canonical[key]:
            if key in seen:
                raise ValueError("freezer repeated canonical RTL payload: " + key)
            seen.add(key)
            result.append((expected_destination, source, kind))
    if seen != set(expected):
        raise ValueError("freezer lost a canonical RTL payload")
    return result
