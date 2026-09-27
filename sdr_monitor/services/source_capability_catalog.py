"""Retained low-rate providers over the EXISTING immutable capability inventory."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, contextmanager
from enum import StrEnum
from itertools import islice
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .tinysa_owned_acquisition import TinySaOwnedAcquisition

from ..domain.device_capabilities import (
    AdapterRuntimeSnapshot,
    DeviceCapabilityBinding,
    DeviceCapabilityInventory,
    DeviceCapabilitySnapshot,
    DeviceFamily,
    build_device_capability_inventory,
    merge_device_capability_inventories,
)


class CapabilityCatalogReason(StrEnum):
    BUSY = "busy"
    RELEASE_PENDING = "release_pending"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_CONTRACT = "provider_contract"
    SOURCE_NOT_FOUND = "source_not_found"
    CLOSE_FAILED = "close_failed"


class CapabilityCatalogError(RuntimeError):
    def __init__(self, reason: CapabilityCatalogReason) -> None:
        self.reason = CapabilityCatalogReason(reason)
        super().__init__(f"Source capability catalog refused: {self.reason.value}")


class CapabilityCatalogProvider(Protocol):
    adapter_id: str
    family: DeviceFamily

    @property
    def cleanup_pending(self) -> bool: ...
    def runtime_snapshot(self) -> AdapterRuntimeSnapshot: ...
    def discover(self, *, startup_only: bool) -> DeviceCapabilityInventory: ...
    def observe_source(self, source_id: str) -> DeviceCapabilityInventory: ...
    def close(self) -> None: ...


class SourceCapabilityCatalog:
    """Keep provider owners across failures; no implicit Stop/retry/substitution.

    Every effecting operation holds the supplied shared control transaction.
    It must exclude all source owners in the composing application. This is
    not a process-global USB lock and cannot control unrelated SDK callers.
    Provider runtime methods and snapshot lookup must perform no hardware I/O.
    """

    def __init__(self, providers: Iterable[CapabilityCatalogProvider], *,
                 control_transaction: Callable[[], AbstractContextManager[object]],
                 maximum_devices: int = 32) -> None:
        owned = tuple(islice(providers, 17))
        if not 1 <= len(owned) <= 16 or not callable(control_transaction):
            raise ValueError("catalog needs a bounded provider set and shared control transaction")
        runtimes = tuple(provider.runtime_snapshot() for provider in owned)
        if len({runtime.adapter_id for runtime in runtimes}) != len(runtimes):
            raise ValueError("catalog adapter providers must be unique")
        for provider, runtime in zip(owned, runtimes, strict=True):
            if runtime.adapter_id != provider.adapter_id or runtime.family is not provider.family:
                raise ValueError("catalog provider metadata disagrees with runtime facts")
        self._providers = owned
        self._control_transaction = control_transaction
        self._maximum_devices = maximum_devices
        self._inventory = build_device_capability_inventory((), maximum_devices=maximum_devices, runtimes=runtimes)
        self._parts: dict[str, DeviceCapabilityInventory] = {}
        self._failures: tuple[tuple[str, CapabilityCatalogReason], ...] = ()
        self._operation_lock = threading.Lock()

    @property
    def cleanup_pending(self) -> bool:
        return any(provider.cleanup_pending for provider in self._providers)

    @property
    def last_failures(self) -> tuple[tuple[str, CapabilityCatalogReason], ...]:
        return self._failures  # immutable, scalar, never route/vendor details

    @contextmanager
    def _operation(self):
        if not self._operation_lock.acquire(blocking=False):
            raise CapabilityCatalogError(CapabilityCatalogReason.BUSY)
        try:
            yield
        finally:
            self._operation_lock.release()

    def _require_released(self) -> None:
        if self.cleanup_pending:
            raise CapabilityCatalogError(CapabilityCatalogReason.RELEASE_PENDING)

    def snapshot(self) -> DeviceCapabilityInventory:
        """No SDK/discovery; reject its own busy operation or quarantine."""
        with self._operation():
            self._require_released()
            return self._inventory

    def _empty(self) -> DeviceCapabilityInventory:
        return build_device_capability_inventory(
            (), maximum_devices=self._maximum_devices,
            runtimes=(provider.runtime_snapshot() for provider in self._providers),
        )

    def _validated(self, provider: CapabilityCatalogProvider,
                   inventory: DeviceCapabilityInventory) -> DeviceCapabilityInventory:
        if not isinstance(inventory, DeviceCapabilityInventory):
            raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_CONTRACT)
        values: tuple[DeviceCapabilitySnapshot | DeviceCapabilityBinding | AdapterRuntimeSnapshot, ...] = (
            *inventory.snapshots, *inventory.bindings, *inventory.runtimes,
        )
        if any(value.adapter_id != provider.adapter_id or value.family is not provider.family for value in values):
            raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_CONTRACT)
        if inventory.runtimes != (provider.runtime_snapshot(),):
            raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_CONTRACT)
        return inventory

    def _publish(self) -> DeviceCapabilityInventory:
        try:
            self._inventory = merge_device_capability_inventories(self._parts.values(), maximum_devices=self._maximum_devices)
        except (TypeError, ValueError):
            self._parts = {}
            self._inventory = self._empty()
            raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_CONTRACT) from None
        return self._inventory

    def refresh(self, *, startup_only: bool = False) -> DeviceCapabilityInventory:
        with self._operation(), self._control_transaction():
            self._require_released()
            # Invalidate old physical facts before new side effects. A failed
            # refresh must never leave yesterday's capability truth accepted.
            self._inventory = self._empty()
            self._parts = {}
            failures = []
            for provider in self._providers:
                try:
                    part = self._validated(provider, provider.discover(startup_only=startup_only))
                    self._require_released()
                    self._parts[provider.adapter_id] = part
                except Exception as error:  # noqa: BLE001 - a provider boundary must redact SDK/route details.
                    reason = error.reason if isinstance(error, CapabilityCatalogError) else CapabilityCatalogReason.PROVIDER_FAILED
                    failures.append((provider.adapter_id, reason))
                    self._failures = tuple(failures)
                    if self.cleanup_pending:
                        self._parts = {}
                        raise CapabilityCatalogError(CapabilityCatalogReason.RELEASE_PENDING) from None
                    # A closed failed provider does not erase other families.
                    self._parts[provider.adapter_id] = build_device_capability_inventory(
                        (), runtimes=(provider.runtime_snapshot(),), maximum_devices=self._maximum_devices)
            self._failures = tuple(failures)
            return self._publish()

    def observe_source(self, source_id: str) -> DeviceCapabilityInventory:
        with self._operation(), self._control_transaction():
            self._require_released()
            binding = self._inventory.binding_for_source(source_id)
            if binding is None:
                raise CapabilityCatalogError(CapabilityCatalogReason.SOURCE_NOT_FOUND)
            provider = next(provider for provider in self._providers if provider.adapter_id == binding.adapter_id)
            # Drop this family's old facts before a selected read-only probe.
            self._parts[provider.adapter_id] = build_device_capability_inventory(
                (), runtimes=(provider.runtime_snapshot(),), maximum_devices=self._maximum_devices)
            self._publish()
            try:
                part = self._validated(provider, provider.observe_source(source_id))
                self._require_released()
            except Exception as error:  # noqa: BLE001 - retain failed ownership, never leak vendor details.
                reason = error.reason if isinstance(error, CapabilityCatalogError) else CapabilityCatalogReason.PROVIDER_FAILED
                self._failures = ((provider.adapter_id, reason),)
                if self.cleanup_pending:
                    reason = CapabilityCatalogReason.RELEASE_PENDING
                raise CapabilityCatalogError(reason) from None
            self._parts[provider.adapter_id] = part
            self._publish()
            self._failures = ()
            return self._inventory

    def prepare_tinysa_acquisition(self, binding: DeviceCapabilityBinding,
                                  runtime: AdapterRuntimeSnapshot) -> TinySaOwnedAcquisition:
        """Reserve a same-provider serial owner from EXACT retained references.

        No new Discover/probe, serial open, measurement or firmware/settings
        effect. This is not a Start permit: the Analyzer must still reserve
        its graph exclusion and enforce mode/input/request admission. An unused
        prepared owner also needs explicit close; all probes refuse meanwhile.
        """
        from .source_capability_providers import TinySaCapabilityProvider

        with self._operation(), self._control_transaction():
            self._require_released()
            if (not isinstance(binding, DeviceCapabilityBinding)
                    or binding.family is not DeviceFamily.TINYSA
                    or self._inventory.binding_for_source(binding.source_id) is not binding
                    or self._inventory.runtime_for_adapter(binding.adapter_id) is not runtime):
                raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_CONTRACT)
            provider = next((value for value in self._providers
                             if value.adapter_id == binding.adapter_id), None)
            if not isinstance(provider, TinySaCapabilityProvider):
                raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_CONTRACT)
            try:
                return provider.prepare_acquisition(binding.source_id, binding)
            except Exception:  # noqa: BLE001 - no routes, serial/vendor failures or substitution.
                raise CapabilityCatalogError(CapabilityCatalogReason.PROVIDER_FAILED) from None

    def close(self) -> None:
        """Explicit release only. Never Stop an unrelated stream owner."""
        with self._operation(), self._control_transaction():
            for provider in self._providers:
                try:
                    provider.close()
                except Exception:  # noqa: BLE001 - explicit close remains retryable on its same provider.
                    self._inventory = self._empty()
                    self._parts = {}
                    raise CapabilityCatalogError(CapabilityCatalogReason.CLOSE_FAILED) from None
            self._require_released()
            self._parts = {}
            self._inventory = self._empty()


__all__ = ["CapabilityCatalogError", "CapabilityCatalogProvider", "CapabilityCatalogReason", "SourceCapabilityCatalog"]
