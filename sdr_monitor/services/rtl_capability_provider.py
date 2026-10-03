"""Optional unbundled RTL runtime observation, never startup SDK loading.

The opt-in native module contains locally declared ABI signatures only. The
separate external DLL set is manually provisioned and SHA-256 admitted. This
does not clear GPL redistribution/linking review or prove a working driver.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from ..domain.device_capabilities import (
    AdapterRuntimeAvailability,
    AdapterRuntimeSnapshot,
    DeviceCapabilityBinding,
    DeviceCapabilityInventory,
    DeviceFamily,
    RtlSessionRouteAssurance,
    build_device_capability_inventory,
)

RTL_ADAPTER_ID = "rtl.librtlsdr.rx.v1"
RTL_SOURCE_ID = "rtl-sdr-session"
_MAX_MANIFEST_BYTES = 16_384


class RtlNativePort(Protocol):
    RTLSDR_OFFICIAL_COMPILED: bool
    RTLSDR_RX_CONTROL_CONTRACT_VERSION: int
    __file__: str

    def RtlExternalFile(self, path: str, sha256_hex: str) -> object: ...
    def RtlExternalRuntime(self, library: object, dependencies: list[object]) -> object: ...
    DetectorType: Any
    def RtlSessionRoute(self, manufacturer: str, product: str, serial: str,
                        tuner_type: int, selection_revision: int) -> object: ...
    def rtl_enumerate_candidates(self, runtime: object) -> list[RtlCandidate]: ...
    def rtl_observe_single_candidate(self, runtime: object) -> RtlCandidate: ...
    def create_rtl_runtime_control(self, *args: object, **kwargs: object) -> RtlControlPort: ...
    def rtl_process_is_quarantined(self) -> bool: ...


class RtlCandidate(Protocol):
    enumeration_index: int
    manufacturer: str
    product: str
    serial: str
    tuner_type: int
    direct_sampling: bool
    offset_tuning: bool


class RtlReadback(Protocol):
    session_epoch: int
    actual_center_hz: int
    actual_sample_rate_hz: int
    tuner_gain_readback_known: bool
    cached_tuner_gain_tenth_db: int | None


class RtlStop(Protocol):
    def complete(self) -> bool: ...


class RtlControlPort(Protocol):
    def readback(self) -> RtlReadback: ...
    def drain_latest_spectrum_frame(self) -> Any: ...
    def metrics(self) -> Any: ...
    def stop(self, timeout_ms: int) -> RtlStop: ...
    def cleanup_required(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RtlRuntimeProvision:
    native: RtlNativePort
    runtime: object
    module_sha256: str
    runtime_set_sha256: str
    manual_gain_contract_version: int | None = None

    def __post_init__(self) -> None:
        version = self.manual_gain_contract_version
        observed = getattr(self.native, "RTLSDR_TUNER_GAIN_CONTRACT_VERSION", None)
        if version is not None and (type(version) is not int or version != 1
                or type(observed) is not int or observed != version):
            raise ValueError("RTL manual gain provision requires the exact native bridge version")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def qualified_rtl_runtime(native: object) -> RtlRuntimeProvision | None:
    """Static app-local path/hash qualification; does not load SDK or probe USB."""
    if (getattr(native, "RTLSDR_OFFICIAL_COMPILED", None) is not True
            or type(getattr(native, "RTLSDR_RX_CONTROL_CONTRACT_VERSION", None)) is not int
            or getattr(native, "RTLSDR_RX_CONTROL_CONTRACT_VERSION", None) != 1
            or not all(callable(getattr(native, name, None)) for name in (
                "RtlExternalFile", "RtlExternalRuntime", "rtl_enumerate_candidates",
                "rtl_observe_single_candidate", "create_rtl_runtime_control",
                "rtl_process_is_quarantined"))):
        return None
    location = getattr(native, "__file__", None)
    if not isinstance(location, str):
        return None
    try:
        module = Path(location).resolve(strict=True)
        folder = module.parent
        build_file = folder / "native_build_manifest.json"
        external_file = folder / "rtl_external_runtime.json"
        if build_file.stat().st_size > _MAX_MANIFEST_BYTES or external_file.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        build = json.loads(build_file.read_text(encoding="utf-8-sig"))
        external = json.loads(external_file.read_text(encoding="utf-8-sig"))
        module_hash = _sha256(module)
        if (not isinstance(build, dict) or build.get("rtl_official_compiled") is not True
                or type(build.get("rtl_control_contract_version")) is not int
                or build["rtl_control_contract_version"] != 1
                or build.get("artifact_sha256") != module_hash
                or not isinstance(external, dict) or set(external) != {"library", "dependencies"}
                or not isinstance(external["dependencies"], dict)
                or len(external["dependencies"]) > 8):
            return None
        declared_gain = build.get("rtl_tuner_gain_contract_version")
        observed_gain = getattr(native, "RTLSDR_TUNER_GAIN_CONTRACT_VERSION", None)
        if "rtl_tuner_gain_contract_version" in build or hasattr(native, "RTLSDR_TUNER_GAIN_CONTRACT_VERSION"):
            if (type(declared_gain) is not int or declared_gain != 1
                    or type(observed_gain) is not int or observed_gain != declared_gain):
                return None
        hashes = {"rtlsdr.dll": external["library"], **external["dependencies"]}
        if (len(hashes) != 1 + len(external["dependencies"])
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.dll", name)
                       or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                       for name, digest in hashes.items())):
            return None
        typed_native = cast(RtlNativePort, native)
        files: list[object] = []
        for name, digest in hashes.items():
            path = (folder / name).resolve(strict=True)
            if path.parent != folder or _sha256(path) != digest:
                return None
            files.append(typed_native.RtlExternalFile(str(path), digest))
        runtime = typed_native.RtlExternalRuntime(files[0], files[1:])
        runtime_set_hash = hashlib.sha256((module_hash + "\n" +
            "\n".join(f"{name}:{digest}" for name, digest in sorted(hashes.items()))).encode("ascii")).hexdigest()
        return RtlRuntimeProvision(typed_native, runtime, module_hash, runtime_set_hash, declared_gain)
    except (OSError, TypeError, ValueError, AttributeError, json.JSONDecodeError):
        return None


class RtlCapabilityProvider:
    adapter_id = RTL_ADAPTER_ID
    family = DeviceFamily.RTL_SDR

    def __init__(self, provision: RtlRuntimeProvision | None) -> None:
        self._provision = provision
        self._revision = 0
        self._candidate: RtlCandidate | None = None
        self._route: RtlSessionRouteAssurance | None = None
        self._runtime = AdapterRuntimeSnapshot(self.adapter_id, self.family,
            AdapterRuntimeAvailability.AVAILABLE if provision is not None else AdapterRuntimeAvailability.UNAVAILABLE,
            "external-rtl-contract1-hash-admitted" if provision is not None else "external-rtl-runtime-unavailable")

    @property
    def cleanup_pending(self) -> bool:
        return bool(self._provision is not None and self._provision.native.rtl_process_is_quarantined())

    def runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._runtime

    def discover(self, *, startup_only: bool) -> DeviceCapabilityInventory:
        self._candidate = self._route = None
        self._revision += 1
        if startup_only or self._provision is None:
            return build_device_capability_inventory((), runtimes=(self._runtime,))
        candidates = self._provision.native.rtl_enumerate_candidates(self._provision.runtime)
        if len(candidates) != 1 or getattr(candidates[0], "enumeration_index", None) != 0:
            return build_device_capability_inventory((), runtimes=(self._runtime,))
        self._candidate = candidates[0]
        return self._inventory()

    def _inventory(self) -> DeviceCapabilityInventory:
        if self._candidate is None:
            return build_device_capability_inventory((), runtimes=(self._runtime,))
        return build_device_capability_inventory((), runtimes=(self._runtime,), bindings=(
            DeviceCapabilityBinding(RTL_SOURCE_ID, self.family, self.adapter_id,
                                    rtl_session_route=self._route),))

    def observe_source(self, source_id: str) -> DeviceCapabilityInventory:
        provision, before = self._provision, self._candidate
        self._route = None
        if provision is None or source_id != RTL_SOURCE_ID or before is None:
            raise RuntimeError("RTL selected observation has no current discovery")
        observed = provision.native.rtl_observe_single_candidate(provision.runtime)
        if (getattr(observed, "enumeration_index", None) != 0
                or any(getattr(observed, name, None) != getattr(before, name, None)
                       for name in ("manufacturer", "product", "serial"))
                or getattr(observed, "direct_sampling", None) is not False
                or getattr(observed, "offset_tuning", None) is not False):
            raise RuntimeError("RTL selected session topology changed")
        self._revision += 1
        self._route = RtlSessionRouteAssurance(observed.manufacturer, observed.product,
            observed.serial, observed.tuner_type, self._revision, True, provision.runtime_set_sha256)
        gain_version = getattr(provision.native, "RTLSDR_TUNER_GAIN_CONTRACT_VERSION", None)
        if provision.manual_gain_contract_version == 1 and type(gain_version) is int and gain_version == 1:
            # Optional bad/absent manual capability must preserve the Auto lane.
            values = getattr(observed, "tuner_gains_tenth_db", ())
            gains = tuple(values) if type(values) in (list, tuple) and len(values) <= 256 else ()
            if observed.tuner_type == 4:
                gains = ()  # FC2580 sentinel is not manual-gain capability.
            try:
                self._route = RtlSessionRouteAssurance(observed.manufacturer, observed.product,
                    observed.serial, observed.tuner_type, self._revision, True,
                    provision.runtime_set_sha256, 1, gains)
            except ValueError:
                pass  # base selected route is valid; no manual table admitted
        return self._inventory()

    def provision_for(self, binding: DeviceCapabilityBinding) -> RtlRuntimeProvision:
        if (self._provision is None or binding.source_id != RTL_SOURCE_ID
                or binding.rtl_session_route is None or binding.rtl_session_route is not self._route
                or self.cleanup_pending
                or binding.rtl_session_route.runtime_set_sha256 != self._provision.runtime_set_sha256):
            raise RuntimeError("RTL selected session assurance is stale")
        return self._provision

    def close(self) -> None:
        self._candidate = self._route = None


__all__ = ["RTL_ADAPTER_ID", "RTL_SOURCE_ID", "RtlCapabilityProvider", "RtlRuntimeProvision",
           "qualified_rtl_runtime"]
