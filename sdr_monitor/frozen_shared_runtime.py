"""Load-only Windows package qualification, never SDK init or a radio probe.

File hashes and a bounded loaded-module inventory are not in-memory binary
attestation. This diagnostic does not qualify RF, throughput or device access.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .libiio_runtime import LIBIIO_RUNTIME_COMPONENTS, configure_frozen_libiio_runtime

HACKRF_COMPONENTS = ("hackrf.dll", "libusb-1.0.dll", "pthreadVC3.dll")
SHARED_COMPONENTS = (*LIBIIO_RUNTIME_COMPONENTS, "hackrf.dll", "pthreadVC3.dll")
REQUIRED_LOADED = frozenset(("libiio.dll", *HACKRF_COMPONENTS))


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def hold_package_libiio_metadata(path: Path) -> Iterator[None]:
    """Own one DLL load reference while native's temporary metadata ref closes.

    No SDK symbol is called here. Never retry an ambiguous FreeLibrary failure.
    This owner belongs only to the short-lived frozen diagnostic process.
    """
    if os.name != "nt":
        raise RuntimeError("metadata DLL ownership requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    kernel.LoadLibraryExW.restype = ctypes.c_void_p
    kernel.FreeLibrary.argtypes = [ctypes.c_void_p]
    kernel.FreeLibrary.restype = ctypes.c_int
    # DLL_LOAD_DIR | DEFAULT_DIRS; use only the already validated absolute path.
    handle = kernel.LoadLibraryExW(str(path.resolve(strict=True)), None, 0x1100)
    if not handle:
        raise RuntimeError("package-local metadata DLL load failed")
    try:
        yield
    finally:
        if not kernel.FreeLibrary(handle):
            raise RuntimeError("metadata DLL reference release was not confirmed")


def windows_loaded_module_paths() -> tuple[Path, ...]:
    """Enumerate every current-process module, not only the first basename.

    Growth/races are bounded. A missing/truncated inventory is a refusal,
    not evidence that a second libusb is absent.
    """
    if os.name != "nt":
        raise RuntimeError("shared runtime inventory requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.K32EnumProcessModules.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel.K32EnumProcessModules.restype = ctypes.c_int
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    process = kernel.GetCurrentProcess()
    capacity = 128
    for _attempt in range(3):
        handles = (ctypes.c_void_p * capacity)()
        needed = ctypes.c_uint32()
        if not kernel.K32EnumProcessModules(process, handles, ctypes.sizeof(handles), ctypes.byref(needed)):
            raise RuntimeError("loaded module inventory failed")
        width = ctypes.sizeof(ctypes.c_void_p)
        if needed.value % width:
            raise RuntimeError("loaded module inventory is malformed")
        count = needed.value // width
        if count > capacity:
            if count > 4096:
                raise RuntimeError("loaded module inventory exceeds bound")
            capacity = count
            continue
        paths: list[Path] = []
        for handle in handles[:count]:
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
            if not length or length >= len(buffer):
                raise RuntimeError("loaded module path is missing or truncated")
            path = Path(buffer.value)
            if not path.is_absolute():
                raise RuntimeError("loaded module path is not absolute")
            paths.append(path.resolve(strict=True))
        return tuple(paths)
    raise RuntimeError("loaded module inventory did not stabilize within bound")


def validate_loaded_shared_runtime(directory: Path, paths: tuple[Path, ...]) -> list[dict[str, str]]:
    """Require one package-local copy of every loaded shared dependency."""
    if len(paths) > 4096:
        raise ValueError("loaded module inventory exceeds bound")
    directory = directory.resolve(strict=True)
    by_name: dict[str, list[Path]] = {name.casefold(): [] for name in SHARED_COMPONENTS}
    for path in paths:
        name = path.name.casefold()
        if name in by_name:
            by_name[name].append(path.resolve(strict=True))
    records: list[dict[str, str]] = []
    for name in SHARED_COMPONENTS:
        matches = by_name[name.casefold()]
        if not matches:
            if name in REQUIRED_LOADED:
                raise ValueError("required shared dependency is not loaded: " + name)
            continue  # Optional libiio transports may be loaded lazily.
        if len(matches) != 1 or matches[0] != (directory / name).resolve(strict=True):
            raise ValueError("shared dependency is duplicate or not package-local: " + name)
        records.append({"name": name, "path": str(matches[0]), "file_sha256": file_sha256(matches[0])})
    return records


def packaged_shared_runtime_verdict(native: object) -> dict[str, object]:
    """Verify sibling manifest/factory, load libiio metadata and inspect DLLs.

    No factory construction, hackrf_init, device list, IIO context, serial
    open, Live engine or GUI is involved. All dependencies stay process-local.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("official shared runtime diagnostic requires a frozen package")
    location = getattr(native, "__file__", None)
    if not isinstance(location, str):
        raise TypeError("native module path is missing")
    module = Path(location).resolve(strict=True)
    directory = module.parent
    with (directory / "native_build_manifest.json").open("rb") as stream:
        encoded = stream.read(16_385)
    if len(encoded) > 16_384:
        raise RuntimeError("native manifest exceeds bound")
    manifest = json.loads(encoded)
    abi = re.fullmatch(r"_sdr_native\.(cp313-win_amd64)\.pyd", module.name)
    if (not isinstance(manifest, dict) or manifest.get("hackrf_official_compiled") is not True
            or type(manifest.get("hackrf_factory_contract_version")) is not int
            or manifest["hackrf_factory_contract_version"] != 2 or manifest.get("cuda_compiled") is not False
            or abi is None or manifest.get("python_abi") != abi.group(1)
            or file_sha256(module) != manifest.get("artifact_sha256")):
        raise RuntimeError("official native identity/ABI/CPU manifest mismatch")
    factory_version = getattr(native, "HACKRF_FACTORY_CONTRACT_VERSION", None)
    if (type(factory_version) is not int or factory_version != 2
            or not callable(getattr(native, "create_hackrf_runtime_dsp_control", None))):
        raise RuntimeError("official HackRF factory2 is unavailable")
    from .services.hackrf_dsp_contract import hackrf_dsp_profile_contract_version, hackrf_persistence_contract_version
    dsp_version = hackrf_dsp_profile_contract_version(native, manifest)
    persistence_version = hackrf_persistence_contract_version(native, manifest)
    hashes = manifest.get("hackrf_runtime_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(HACKRF_COMPONENTS):
        raise RuntimeError("official SDK runtime manifest is incomplete")
    for name in HACKRF_COMPONENTS:
        if file_sha256(directory / name) != hashes[name]:
            raise RuntimeError("official SDK sibling hash mismatch: " + name)
    for key in ("hackrf_header_sha256", "hackrf_library_sha256", "source_commit"):
        width = 40 if key == "source_commit" else 64
        if not isinstance(manifest.get(key), str) or re.fullmatch(r"[0-9a-f]{" + str(width) + "}", manifest[key]) is None:
            raise RuntimeError("official SDK/source identity is invalid: " + key)
    components = configure_frozen_libiio_runtime(native)
    runtime_info = getattr(native, "pluto_runtime_info", None)
    build_info = getattr(native, "build_info", None)
    if (not callable(runtime_info) or not callable(build_info)
            or build_info().get("pluto_compiled") is not True or build_info().get("cuda_compiled") is not False):
        raise RuntimeError("compiled libiio runtime metadata is unavailable")
    files = {name: file_sha256(directory / name) for name in SHARED_COMPONENTS}
    with hold_package_libiio_metadata(components[0]):
        runtime = runtime_info()  # Loads metadata only; no IIO context is created.
        if (not runtime.available or not runtime.library_path
                or Path(runtime.library_path).resolve(strict=True) != components[0]):
            raise RuntimeError("libiio metadata did not use its package-local DLL")
        loaded = validate_loaded_shared_runtime(directory, windows_loaded_module_paths())
    return {
        "schema": "sdr-frozen-shared-runtime-v1",
        "native_module": "sdr_monitor._sdr_native",
        "native_path": str(module),
        "native_sha256": file_sha256(module),
        "native_source_commit": manifest["source_commit"],
        "hackrf_official_compiled": True,
        "hackrf_factory_contract_version": 2,
        "hackrf_dsp_profile_contract_version": dsp_version,
        "hackrf_persistence_contract_version": persistence_version,
        "cuda_compiled": False,
        "libiio_available": True,
        "metadata_hold_released": True,
        "runtime_file_sha256": files,
        "loaded_shared_modules": loaded,
        "factory_constructed": False,
        "sdk_initialized": False,
        "discovery_attempted": False,
        "rx_attempted": False,
        "scope": "Loaded module paths and current file hashes; not in-memory, hardware or RF attestation.",
    }
