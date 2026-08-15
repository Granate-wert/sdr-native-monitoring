"""Read-only tinySA spectrum-trace capability mapping over an injected port.

This adapter deliberately has no serial/USB implementation.  Its port admits
one read-only identity probe and mandatory close only; it never requests a
sweep, changes instrument settings, writes firmware, or resets a device.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..domain.device_capabilities import (
    AcquisitionKind,
    CapabilityEvidence,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityRange,
    CapabilityTransport,
    DeviceCalibrationIdentity,
    DeviceCapabilitySnapshot,
    DeviceFamily,
    stable_identity_key,
)

TINYSA_READ_ONLY_ADAPTER_ID = "native.tinysa.usb_cdc_serial.v1"
_GENERIC_FAILURE = "tinySA capability observation failed closed"
_VENDOR_SPEC_REFERENCE = "tinysa-vendor-specification"
_VENDOR_MODEL_REFERENCE = "tinysa-vendor-model-comparison"


class TinySaModel(StrEnum):
    """The two tinySA analyzer families intentionally admitted by this adapter."""

    BASIC = "tinysa_basic"
    ULTRA = "tinysa_ultra"


@dataclass(frozen=True, slots=True, repr=False)
class TinySaReadOnlyProbe:
    """Redaction-sensitive identity facts returned by a future concrete port."""

    model: TinySaModel
    device_identity: str
    firmware_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", TinySaModel(self.model))
        object.__setattr__(
            self,
            "device_identity",
            _normalized_probe_text(self.device_identity, "tinySA device identity"),
        )
        object.__setattr__(
            self,
            "firmware_version",
            _normalized_probe_text(self.firmware_version, "tinySA firmware version"),
        )


class TinySaReadOnlyProbePort(Protocol):
    """The whole concrete-port surface admitted by this software-only slice."""

    def probe(self) -> TinySaReadOnlyProbe: ...

    def close(self) -> None: ...


class TinySaCapabilityObservationError(RuntimeError):
    """A redacted failure that never forwards transport or instrument details."""


@dataclass(frozen=True, slots=True)
class TinySaAnalyzerSemantics:
    """Fixed analyzer-unit semantics; this is not an SDR I/Q calibration profile."""

    reported_unit: str = "dBm"
    value_provenance: str = "device_reported_trace"
    device_calibration_provenance: str = "device_reported_builtin"
    optional_external_correction_supported: bool = True
    external_correction_requires_separate_layer: bool = True
    raw_iq_available: bool = False
    dbfs_conversion_available: bool = False
    metrological_accuracy_verified: bool = False

    def __post_init__(self) -> None:
        if self.reported_unit != "dBm":
            raise ValueError("tinySA reported unit must be device-reported dBm")
        if self.value_provenance != "device_reported_trace":
            raise ValueError("tinySA values must retain device-reported trace provenance")
        if self.device_calibration_provenance != "device_reported_builtin":
            raise ValueError("tinySA calibration provenance must remain device-reported builtin")
        if not self.optional_external_correction_supported:
            raise ValueError("tinySA must permit a separate optional external correction layer")
        if not self.external_correction_requires_separate_layer:
            raise ValueError("tinySA external correction must remain separate from device-reported dBm")
        if self.raw_iq_available or self.dbfs_conversion_available:
            raise ValueError("tinySA trace semantics must not imply raw I/Q or dBFS conversion")
        if self.metrological_accuracy_verified:
            raise ValueError("tinySA software observation cannot verify metrological accuracy")


@dataclass(frozen=True, slots=True)
class TinySaCapabilityObservation:
    snapshot: DeviceCapabilitySnapshot
    external_correction_identity: DeviceCalibrationIdentity
    analyzer_semantics: TinySaAnalyzerSemantics

    def __post_init__(self) -> None:
        if self.snapshot.family is not DeviceFamily.TINYSA:
            raise ValueError("tinySA observation requires the tinySA device family")
        if self.snapshot.acquisition_kinds != (AcquisitionKind.SPECTRUM_TRACE,):
            raise ValueError("tinySA observation requires spectrum-trace acquisition only")
        if self.snapshot.raw_iq_available is not False:
            raise ValueError("tinySA observation must explicitly deny raw I/Q")
        identity = self.external_correction_identity
        if identity.family is not self.snapshot.family:
            raise ValueError("external correction identity family must match the capability snapshot")
        if identity.adapter_id != self.snapshot.adapter_id:
            raise ValueError("external correction identity adapter must match the capability snapshot")
        if identity.device_identity_key != self.snapshot.identity_key:
            raise ValueError("external correction identity device key must match the capability snapshot")


class TinySaCapabilityAdapter:
    """Map one temporary read-only analyzer port without issuing any command."""

    def __init__(self, port_factory: Callable[[], TinySaReadOnlyProbePort]) -> None:
        if not callable(port_factory):
            raise TypeError("tinySA port factory must be callable")
        self._port_factory = port_factory

    def observe(self) -> TinySaCapabilityObservation:
        port: TinySaReadOnlyProbePort | None = None
        probe: TinySaReadOnlyProbe | None = None
        failed = False
        try:
            port = self._port_factory()
            probe = port.probe()
            if not isinstance(probe, TinySaReadOnlyProbe):
                failed = True
        except Exception:  # noqa: BLE001 - an injected device boundary must fail closed.
            failed = True
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:  # noqa: BLE001 - a failed close invalidates the observation.
                    failed = True
        if failed or probe is None:
            raise TinySaCapabilityObservationError(_GENERIC_FAILURE)

        try:
            identity_key = stable_identity_key(
                f"tinysa-device|{probe.model.value}|{probe.device_identity.casefold()}"
            )
            firmware_fingerprint = stable_identity_key(
                f"tinysa-firmware|{probe.model.value}|{probe.firmware_version}"
            )
            snapshot = DeviceCapabilitySnapshot(
                device_id=f"tinysa-{identity_key[7:23]}",
                identity_key=identity_key,
                label=_label_for(probe.model),
                family=DeviceFamily.TINYSA,
                adapter_id=TINYSA_READ_ONLY_ADAPTER_ID,
                transports=(CapabilityTransport.USB,),
                acquisition_kinds=(AcquisitionKind.SPECTRUM_TRACE,),
                tuning_ranges_hz=_tuning_ranges_for(probe.model),
                raw_iq_available=False,
                evidence=(
                    CapabilityEvidence(
                        CapabilityField.TRANSPORT,
                        CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY,
                        "tinysa-read-only-port",
                    ),
                    CapabilityEvidence(
                        CapabilityField.ACQUISITION_KIND,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        _VENDOR_SPEC_REFERENCE,
                    ),
                    CapabilityEvidence(
                        CapabilityField.TUNING_RANGE,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        _VENDOR_MODEL_REFERENCE,
                    ),
                    CapabilityEvidence(
                        CapabilityField.RAW_IQ,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        _VENDOR_SPEC_REFERENCE,
                    ),
                ),
            )
            correction_identity = DeviceCalibrationIdentity(
                family=DeviceFamily.TINYSA,
                adapter_id=TINYSA_READ_ONLY_ADAPTER_ID,
                device_identity_key=identity_key,
                firmware_fingerprint=firmware_fingerprint,
            )
            return TinySaCapabilityObservation(
                snapshot,
                correction_identity,
                TinySaAnalyzerSemantics(),
            )
        except (TypeError, ValueError):
            raise TinySaCapabilityObservationError(_GENERIC_FAILURE) from None


def _normalized_probe_text(value: object, label: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError(f"{label} must be normalized text")
    if len(value) > 128 or any(
        token in value.casefold() for token in ("usb:", "ip:", "serial:", "\\", "/")
    ):
        raise ValueError(f"{label} must not contain a route or path")
    if value.casefold() in {"unknown", "none", "n/a", "-"}:
        raise ValueError(f"{label} must be known")
    return value


def _label_for(model: TinySaModel) -> str:
    if model is TinySaModel.BASIC:
        return "tinySA spectrum analyzer"
    if model is TinySaModel.ULTRA:
        return "tinySA Ultra spectrum analyzer"
    raise ValueError("unsupported tinySA model")


def _tuning_ranges_for(model: TinySaModel) -> tuple[CapabilityRange, ...]:
    if model is TinySaModel.BASIC:
        # tinySA Basic exposes separate low and high input ranges.
        return (CapabilityRange(100e3, 350e6, "Hz"), CapabilityRange(240e6, 960e6, "Hz"))
    if model is TinySaModel.ULTRA:
        # Ultra mode is required for the upper part of this declared range.
        return (CapabilityRange(100e3, 5.3e9, "Hz"),)
    raise ValueError("unsupported tinySA model")


__all__ = [
    "TINYSA_READ_ONLY_ADAPTER_ID",
    "TinySaAnalyzerSemantics",
    "TinySaCapabilityAdapter",
    "TinySaCapabilityObservation",
    "TinySaCapabilityObservationError",
    "TinySaModel",
    "TinySaReadOnlyProbe",
    "TinySaReadOnlyProbePort",
]
