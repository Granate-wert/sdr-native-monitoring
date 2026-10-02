"""Common source choice/observation; hardware acquisition remains family-owned."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from ..domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceCapabilityBinding, DeviceCapabilityInventory, DeviceFamily
from ..domain.live import DeviceDescriptor, LiveAdmissionRejected, LiveSnapshot


class SourceCatalogPort(Protocol):
    @property
    def cleanup_pending(self) -> bool: ...
    def refresh(self, *, startup_only: bool = False) -> DeviceCapabilityInventory: ...
    def snapshot(self) -> DeviceCapabilityInventory: ...
    def observe_source(self, source_id: str) -> DeviceCapabilityInventory: ...
    def close(self) -> None: ...


class NativeSourceSelectionPort(Protocol):
    def discovered_devices(self) -> tuple[DeviceDescriptor, ...]: ...
    def latest_snapshot(self) -> LiveSnapshot: ...
    def select_manual_uri(self, uri: str) -> LiveSnapshot: ...


class AnalyzerSourceSelectionApplicationService:
    """One current low-rate selection, no receiver/worker or fake Pluto config.

    SDK operations must run in the presenter's control worker. Current is an
    immutable, no-I/O read even while observation is pending. Selection carries
    existing binding/runtime references; revision cannot imply RF continuity.
    """

    def __init__(self, catalog: SourceCatalogPort, native: NativeSourceSelectionPort, *,
                 control_transaction: Callable[[], AbstractContextManager[object]]) -> None:
        self._catalog, self._native = catalog, native
        self._control_transaction = control_transaction
        self._state = AnalyzerSourceSelection()

    def current(self) -> AnalyzerSourceSelection:
        return self._state

    def _invalidate(self) -> int:
        revision = self._state.revision + 1
        if revision > (1 << 64) - 1:
            raise OverflowError("source selection revision exhausted")
        self._state = AnalyzerSourceSelection(revision, self._state.choices)
        return revision

    def _failed(self, revision: int) -> None:
        pending = self._catalog.cleanup_pending
        self._state = AnalyzerSourceSelection(revision, release_pending=pending,
            refusal="release_pending" if pending else "observation_failed")

    def _choices(self, inventory: DeviceCapabilityInventory) -> tuple[AnalyzerSourceChoice, ...]:
        native = {value.device_id: value for value in self._native.discovered_devices()}
        values = []
        for binding in inventory.bindings:
            descriptor = native.get(binding.source_id) if binding.family is DeviceFamily.AD936X else None
            if descriptor is not None:
                transport = descriptor.transport.value.upper()
            elif binding.snapshot is not None:
                transport = "/".join(value.value.upper() for value in binding.snapshot.transports) or "UNKNOWN"
            else:
                transport = "UNVERIFIED"
            family_label = {DeviceFamily.AD936X: "AD936x SDR", DeviceFamily.HACKRF: "HackRF One",
                            DeviceFamily.TINYSA: "tinySA candidate",
                            DeviceFamily.RTL_SDR: "RTL-SDR USB session"}.get(binding.family, "SDR/instrument candidate")
            if binding.family is DeviceFamily.RTL_SDR:
                transport = "USB SESSION" if binding.rtl_session_route is not None else "USB UNVERIFIED"
            if binding.snapshot is not None:
                family_label = binding.snapshot.label
            # Fixed model/transport + opaque suffix: no USB serial, COM, URI.
            label = f"{family_label} · {transport} · {binding.source_id[-8:]}"
            values.append(AnalyzerSourceChoice(binding, inventory.runtime_for_adapter(binding.adapter_id), label, transport))
        return tuple(values)

    def discover(self, *, startup: bool = False) -> tuple[AnalyzerSourceChoice, ...]:
        with self._control_transaction():
            revision = self._invalidate()
            try:
                choices = self._choices(self._catalog.refresh(startup_only=startup))
            except Exception:  # noqa: BLE001 - preserve quarantine and redact SDK details at the public boundary.
                self._failed(revision)
                raise RuntimeError("Source discovery failed closed") from None
            self._state = AnalyzerSourceSelection(revision, choices)
            return choices

    def select(self, source_id: str) -> None:
        with self._control_transaction():
            if not any(choice.device_id == source_id for choice in self._state.choices):
                raise LiveAdmissionRejected("Selected source is not in the current catalog")
            revision = self._invalidate()
            try:
                choices = self._choices(self._catalog.observe_source(source_id))
                selected = next(choice for choice in choices if choice.device_id == source_id)
                if selected.family is DeviceFamily.AD936X:
                    snapshot = self._native.latest_snapshot()
                    if snapshot.error is not None or snapshot.device is None or snapshot.device.device_id != source_id:
                        raise ValueError("native selected observation is not the selected operational source")
                self._state = AnalyzerSourceSelection(revision, choices, source_id)
            except Exception:  # noqa: BLE001 - no old source/config/frame may remain accepted after failed selection.
                self._failed(revision)
                raise RuntimeError("Source selection failed closed") from None

    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        """Keep explicit native manual routing, no inferred catalog alias."""
        with self._control_transaction():
            revision = self._invalidate()
            try:
                snapshot = self._native.select_manual_uri(uri)
                descriptor = snapshot.device
                if snapshot.error is not None or descriptor is None:
                    raise ValueError("manual native source selection was refused")
                runtime = next(value for value in self._catalog.snapshot().runtimes if value.family is DeviceFamily.AD936X)
                binding = DeviceCapabilityBinding(descriptor.device_id, DeviceFamily.AD936X, runtime.adapter_id,
                                                  descriptor.capability_snapshot, descriptor.calibration_identity)
                transport = descriptor.transport.value.upper()
                choice = AnalyzerSourceChoice(binding, runtime, f"AD936x SDR · {transport} · {binding.source_id[-8:]}", transport)
                # A manual operational binding is not inserted into catalog
                # truth or automatically joined to a USB/IP alias.
                choices = tuple(value for value in self._state.choices if value.device_id != choice.device_id)
                choices = (*choices, choice)
                self._state = AnalyzerSourceSelection(revision, choices, choice.device_id)
                return snapshot
            except Exception:  # noqa: BLE001 - manual failure is redacted and clears stale selection too.
                self._failed(revision)
                raise RuntimeError("Manual AD936x source selection failed closed") from None

    def require_ad936x_controls(self) -> None:
        if not self._state.ad936x_controls_available:
            raise LiveAdmissionRejected("Selected family requires its own acquisition/configuration path")

    def release_pending(self) -> None:
        if not self._state.release_pending:
            return
        with self._control_transaction():
            revision = self._invalidate()
            try:
                self._catalog.close()
            except Exception:  # noqa: BLE001 - explicit close must preserve pending ownership and visible failure.
                self._failed(revision)
                raise RuntimeError("Source release failed closed") from None
            self._state = AnalyzerSourceSelection(revision)


__all__ = ["AnalyzerSourceSelectionApplicationService", "NativeSourceSelectionPort", "SourceCatalogPort"]
