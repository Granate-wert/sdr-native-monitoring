"""Concrete retained family seams for the common V2 capability catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from ..domain.device_capabilities import (
    AdapterRuntimeAvailability,
    AdapterRuntimeSnapshot,
    DeviceCapabilityBinding,
    DeviceCapabilityInventory,
    DeviceFamily,
    build_device_capability_inventory,
)
from .ad936x_capability_adapter import AD936X_LIBIIO_ADAPTER_ID
from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID, HackrfCapabilityAdapter
from .libhackrf_read_only import LibhackrfReadOnlyPort
from .native_live import NativeLiveSessionService
from .source_capability_catalog import SourceCapabilityCatalog
from .tinysa_capability_adapter import TINYSA_READ_ONLY_ADAPTER_ID, TinySaCapabilityAdapter, TinySaCapabilityObservation
from .tinysa_owned_acquisition import TinySaOwnedAcquisition
from .tinysa_readonly_port import TinySaSerialReadOnlyPort
from .tinysa_serial_source_backend import TinySaSerialSourceBackend
from .tinysa_source_composition import MAX_TINYSA_DISCOVERED_SOURCES, TinySaTransportEndpoint


class NativeLiveCapabilityProvider:
    adapter_id = AD936X_LIBIIO_ADAPTER_ID
    family = DeviceFamily.AD936X

    def __init__(self, live: NativeLiveSessionService) -> None:
        self._live = live

    @property
    def cleanup_pending(self) -> bool:
        return self._live.capability_cleanup_pending

    def runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        # This surface read does not depend on publishing a pending catalog.
        from .native_live import _ad936x_runtime_snapshot
        return _ad936x_runtime_snapshot(self._live._native)

    def discover(self, *, startup_only: bool) -> DeviceCapabilityInventory:
        (self._live.discover_startup_devices if startup_only else self._live.discover_devices)()
        return self._live.capability_inventory()

    def observe_source(self, source_id: str) -> DeviceCapabilityInventory:
        result = self._live.select_device(source_id)
        if result.error is not None:
            raise RuntimeError("AD936x selected observation failed closed")
        return self._live.capability_inventory()

    def close(self) -> None:
        self._live.close_capability_observation()


class HackrfCapabilityProvider:
    adapter_id = HACKRF_LIBHACKRF_ADAPTER_ID
    family = DeviceFamily.HACKRF

    def __init__(self, adapter: HackrfCapabilityAdapter | None,
                 runtime: AdapterRuntimeSnapshot) -> None:
        self._adapter, self._runtime = adapter, runtime

    @property
    def cleanup_pending(self) -> bool:
        return self._adapter is not None and self._adapter.cleanup_pending

    def runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._runtime

    def discover(self, *, startup_only: bool) -> DeviceCapabilityInventory:
        if self._adapter is None:
            return build_device_capability_inventory((), runtimes=(self._runtime,))
        observed = self._adapter.observe()
        snapshot = observed.snapshot
        return build_device_capability_inventory((snapshot,), runtimes=(self._runtime,),
            bindings=(DeviceCapabilityBinding(snapshot.device_id, self.family, self.adapter_id,
                                               snapshot, observed.calibration_identity),))

    def observe_source(self, source_id: str) -> DeviceCapabilityInventory:
        inventory = self.discover(startup_only=False)
        if inventory.binding_for_source(source_id) is None:
            raise RuntimeError("HackRF selected identity changed")
        return inventory

    def close(self) -> None:
        if self._adapter is not None:
            self._adapter.close()


class TinySaCapabilityProvider:
    adapter_id = TINYSA_READ_ONLY_ADAPTER_ID
    family = DeviceFamily.TINYSA

    def __init__(self, backend: TinySaSerialSourceBackend | None = None, *,
                 port_factory: Callable[[TinySaTransportEndpoint], TinySaSerialReadOnlyPort] | None = None,
                 acquisition_factory: Callable[[TinySaSerialSourceBackend, TinySaTransportEndpoint,
                                               TinySaCapabilityObservation], TinySaOwnedAcquisition] = TinySaOwnedAcquisition) -> None:
        self._backend = backend if backend is not None else TinySaSerialSourceBackend()
        self._port_factory = port_factory or (lambda endpoint: TinySaSerialReadOnlyPort(self._backend, endpoint))
        self._endpoints: dict[str, TinySaTransportEndpoint] = {}
        self._adapters: dict[str, TinySaCapabilityAdapter] = {}
        self._observations: dict[str, TinySaCapabilityObservation] = {}
        self._acquisition_factory = acquisition_factory
        self._acquisitions: dict[str, TinySaOwnedAcquisition] = {}
        self._runtime = AdapterRuntimeSnapshot(self.adapter_id, self.family,
            AdapterRuntimeAvailability.AVAILABLE, "bounded-version-and-scanraw-serial-contract")

    @property
    def cleanup_pending(self) -> bool:
        return (any(adapter.cleanup_pending for adapter in self._adapters.values())
                or any(owner.cleanup_pending for owner in self._acquisitions.values()))

    def runtime_snapshot(self) -> AdapterRuntimeSnapshot:
        return self._runtime

    def discover(self, *, startup_only: bool) -> DeviceCapabilityInventory:
        if self.cleanup_pending:
            raise RuntimeError("tinySA observation requires explicit close")
        endpoints = self._backend.discover_endpoints()
        if len(endpoints) > MAX_TINYSA_DISCOVERED_SOURCES:
            raise RuntimeError("tinySA source inventory exceeds its bound")
        pairs = tuple((f"tinysa-{endpoint.identity_key[7:23]}", endpoint) for endpoint in endpoints)
        if len({source for source, _ in pairs}) != len(pairs):
            raise RuntimeError("tinySA source identity is ambiguous")
        self._endpoints = dict(pairs)
        self._adapters = {}  # all previous temporary owners confirmed closed above
        self._observations = {}
        self._acquisitions = {}  # previous acquisition objects confirmed closed above
        # VID/PID/location alone is only a candidate, never confirmed model/UID.
        return build_device_capability_inventory((), runtimes=(self._runtime,),
            bindings=(DeviceCapabilityBinding(source, self.family, self.adapter_id) for source, _ in pairs))

    def observe_source(self, source_id: str) -> DeviceCapabilityInventory:
        endpoint = self._endpoints.get(source_id)
        if endpoint is None or self.cleanup_pending:
            raise RuntimeError("tinySA selected observation is unavailable")
        adapter = self._adapters.get(source_id)
        if adapter is None:
            adapter = TinySaCapabilityAdapter(lambda: self._port_factory(endpoint))
            self._adapters[source_id] = adapter  # BEFORE port acquisition
        self._observations.pop(source_id, None)
        self._observations[source_id] = adapter.observe()
        bindings = tuple(
            DeviceCapabilityBinding(source, self.family, self.adapter_id,
                self._observations[source].snapshot if source in self._observations else None,
                self._observations[source].external_correction_identity if source in self._observations else None)
            for source in self._endpoints)
        return build_device_capability_inventory((value.snapshot for value in self._observations.values()),
                                                bindings=bindings, runtimes=(self._runtime,))

    def prepare_acquisition(self, source_id: str, binding: DeviceCapabilityBinding) -> TinySaOwnedAcquisition:
        """Reserve a SAME-backend owner without opening serial or changing facts."""
        endpoint = self._endpoints.get(source_id)
        observed = self._observations.get(source_id)
        if (self.cleanup_pending or endpoint is None or observed is None
                or binding.source_id != source_id or binding.family is not self.family
                or binding.adapter_id != self.adapter_id
                or binding.snapshot is not observed.snapshot
                or binding.calibration_identity is not observed.external_correction_identity):
            raise RuntimeError("tinySA retained acquisition binding is unavailable")
        owner = self._acquisition_factory(self._backend, endpoint, observed)
        if not isinstance(owner, TinySaOwnedAcquisition):
            # This injection is an inert construction seam, not an SDK factory.
            raise TypeError("tinySA acquisition factory returned an invalid inert owner")
        self._acquisitions[source_id] = owner
        return owner

    def close(self) -> None:
        first_error: Exception | None = None
        owners: tuple[TinySaOwnedAcquisition | TinySaCapabilityAdapter, ...] = (
            *self._acquisitions.values(), *self._adapters.values())
        for owner in owners:
            try:
                owner.close()
            except Exception as error:  # noqa: BLE001 - attempt independent owners, retain first failure.
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise RuntimeError("tinySA provider release failed; explicit close required") from None


def _qualified_hackrf_sdk_directory(native: object) -> Path | None:
    """Static app-local locator only; no SDK load or in-memory attestation."""
    version = getattr(native, "HACKRF_FACTORY_CONTRACT_VERSION", None)
    if type(version) is not int or version != 2 or not callable(getattr(native, "create_hackrf_runtime_dsp_control", None)):
        return None
    location = getattr(native, "__file__", None)
    if not isinstance(location, str):
        return None
    module = Path(location).resolve()
    folder = module.parent
    try:
        with (folder / "native_build_manifest.json").open("rb") as stream:
            encoded = stream.read(16_385)
        if len(encoded) > 16_384:
            return None
        manifest = json.loads(encoded)
        with module.open("rb") as stream:
            module_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if (not isinstance(manifest, dict) or manifest.get("hackrf_official_compiled") is not True
                or type(manifest.get("hackrf_factory_contract_version")) is not int
                or manifest["hackrf_factory_contract_version"] != 2
                or module_hash != manifest.get("artifact_sha256")):
            return None
        hashes = manifest.get("hackrf_runtime_sha256")
        from .hackrf_dsp_contract import hackrf_dsp_profile_contract_version, hackrf_persistence_contract_version
        from .hackrf_sweep_contract import hackrf_sweep_contract_version
        hackrf_dsp_profile_contract_version(native, manifest)
        hackrf_persistence_contract_version(native, manifest)
        hackrf_sweep_contract_version(native, manifest)
        if not isinstance(hashes, dict) or set(hashes) != {"hackrf.dll", "libusb-1.0.dll", "pthreadVC3.dll"}:
            return None
        for name, expected in hashes.items():
            with (folder / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    return None
    except (OSError, TypeError, ValueError):
        return None
    return folder


def build_source_capability_catalog(live: NativeLiveSessionService) -> SourceCapabilityCatalog:
    """Production DI, not Discover: construction opens no radio or serial port."""
    # The composition boundary uses the SAME already loaded native module.
    # No alternate SDK/native loader or user-machine .tools path is searched.
    folder = _qualified_hackrf_sdk_directory(live._native)
    runtime = AdapterRuntimeSnapshot(HACKRF_LIBHACKRF_ADAPTER_ID, DeviceFamily.HACKRF,
        AdapterRuntimeAvailability.AVAILABLE if folder is not None else AdapterRuntimeAvailability.UNAVAILABLE,
        "official-factory2-and-manifest-matched-sdk" if folder is not None else "official-capability-runtime-unavailable")
    adapter = HackrfCapabilityAdapter(lambda: LibhackrfReadOnlyPort(folder / "hackrf.dll", folder)) if folder is not None else None
    return SourceCapabilityCatalog(
        (NativeLiveCapabilityProvider(live), HackrfCapabilityProvider(adapter, runtime), TinySaCapabilityProvider()),
        control_transaction=live.capability_control_transaction,
    )


__all__ = ["HackrfCapabilityProvider", "NativeLiveCapabilityProvider", "TinySaCapabilityProvider", "build_source_capability_catalog"]
