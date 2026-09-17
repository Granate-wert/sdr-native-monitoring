"""Read-only R11-B mapping from the canonical native AD936x/libiio probe."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math
from typing import Any, SupportsFloat, SupportsIndex, cast

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


AD936X_LIBIIO_ADAPTER_ID = "native.libiio.ad936x.v1"
_GENERIC_FAILURE = "AD936x capability observation failed closed"


class Ad936xCapabilityObservationError(RuntimeError):
    """A route-redacted low-rate adapter failure."""


@dataclass(frozen=True, slots=True)
class Ad936xCapabilityObservation:
    snapshot: DeviceCapabilitySnapshot
    calibration_identity: DeviceCalibrationIdentity

    def __post_init__(self) -> None:
        if self.snapshot.family is not DeviceFamily.AD936X:
            raise ValueError("AD936x observation requires the AD936x device family")
        if self.calibration_identity.family is not self.snapshot.family:
            raise ValueError("calibration identity family must match the capability snapshot")
        if self.calibration_identity.adapter_id != self.snapshot.adapter_id:
            raise ValueError("calibration identity adapter must match the capability snapshot")
        if self.calibration_identity.device_identity_key != self.snapshot.identity_key:
            raise ValueError("calibration identity device key must match the capability snapshot")


class Ad936xLibiioCapabilityAdapter:
    """Own one temporary native context and publish no streaming operations."""

    def __init__(self, native_module: Any, *, timeout_ms: int = 3000) -> None:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        if not callable(getattr(native_module, "PlutoDevice", None)):
            raise ValueError("canonical native module omits PlutoDevice")
        self._native = native_module
        self._timeout_ms = timeout_ms

    def observe(self, route: str) -> Ad936xCapabilityObservation:
        transport = _transport_for_route(route)
        device: Any | None = None
        try:
            device = self._native.PlutoDevice(route, self._timeout_ms)
            probe = device.probe()
            capabilities = device.capabilities()
        except Exception:
            raise Ad936xCapabilityObservationError(_GENERIC_FAILURE) from None
        finally:
            if device is not None:
                try:
                    device.disconnect()
                except Exception:
                    pass

        try:
            serial = _required_native_text(getattr(probe, "serial", None), "serial")
            firmware = _required_native_text(getattr(probe, "firmware", None), "firmware")
            identity_key = stable_identity_key(f"ad936x-serial|{serial.casefold()}")
            firmware_fingerprint = stable_identity_key(f"ad936x-firmware|{firmware}")
            raw_iq = _required_bool(
                getattr(capabilities, "supports_continuous_iq", None),
                "supports_continuous_iq",
            )
            if not raw_iq:
                raise ValueError("the native capability does not admit continuous I/Q")
            evidence = [
                CapabilityEvidence(
                    CapabilityField.TRANSPORT,
                    CapabilityEvidenceOrigin.USER_DECLARATION,
                    "requested-route-class",
                ),
                CapabilityEvidence(
                    CapabilityField.ACQUISITION_KIND,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-continuous-iq-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.TUNING_RANGE,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-tuning-range-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.SAMPLE_RATE_RANGE,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-sample-rate-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.ANALOG_BANDWIDTH_RANGE,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-bandwidth-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.GAIN_RANGE,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-gain-range-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.RAW_IQ,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-continuous-iq-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.HARDWARE_TIMESTAMP,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-timestamp-flag-readback",
                ),
                CapabilityEvidence(
                    CapabilityField.HARDWARE_OVERFLOW_COUNTER,
                    CapabilityEvidenceOrigin.RUNTIME_READBACK,
                    "native-overflow-flag-readback",
                ),
            ]
            rx_channel_count = _observe_rx_channel_count(
                self._native,
                route,
                self._timeout_ms,
            )
            if rx_channel_count is not None:
                evidence.append(
                    CapabilityEvidence(
                        CapabilityField.RX_CHANNEL_COUNT,
                        CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY,
                        "native-scan-pair-topology",
                    )
                )
            snapshot = DeviceCapabilitySnapshot(
                device_id=f"ad936x-{identity_key[7:23]}",
                identity_key=identity_key,
                label="AD936x receiver",
                family=DeviceFamily.AD936X,
                adapter_id=AD936X_LIBIIO_ADAPTER_ID,
                transports=(transport,),
                acquisition_kinds=(AcquisitionKind.COMPLEX_IQ,),
                tuning_ranges_hz=(
                    _native_range(getattr(capabilities, "tuning_range_hz", None), "Hz"),
                ),
                sample_rate_ranges_hz=_native_ranges(
                    getattr(capabilities, "sample_rate_ranges_hz", None), "Hz"
                ),
                analog_bandwidth_ranges_hz=_native_ranges(
                    getattr(capabilities, "analog_bandwidth_ranges_hz", None), "Hz"
                ),
                gain_ranges_db=(
                    _native_range(getattr(capabilities, "gain_range_db", None), "dB"),
                ),
                rx_channel_count=rx_channel_count,
                shared_rx_lo=None,
                raw_iq_available=raw_iq,
                hardware_timestamp_available=_required_bool(
                    getattr(capabilities, "supports_hardware_timestamps", None),
                    "supports_hardware_timestamps",
                ),
                hardware_overflow_counter_available=_required_bool(
                    getattr(capabilities, "supports_overflow_counter", None),
                    "supports_overflow_counter",
                ),
                evidence=tuple(evidence),
            )
            calibration_identity = DeviceCalibrationIdentity(
                family=DeviceFamily.AD936X,
                adapter_id=AD936X_LIBIIO_ADAPTER_ID,
                device_identity_key=identity_key,
                firmware_fingerprint=firmware_fingerprint,
            )
            return Ad936xCapabilityObservation(snapshot, calibration_identity)
        except Exception:
            raise Ad936xCapabilityObservationError(_GENERIC_FAILURE) from None


def _transport_for_route(route: object) -> CapabilityTransport:
    if not isinstance(route, str) or route != route.strip() or not route:
        raise Ad936xCapabilityObservationError(_GENERIC_FAILURE)
    normalized = route.casefold()
    if normalized.startswith("usb:"):
        return CapabilityTransport.USB
    if normalized.startswith("ip:"):
        return CapabilityTransport.ETHERNET
    raise Ad936xCapabilityObservationError(_GENERIC_FAILURE)


def _required_native_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"native {label} is unavailable")
    normalized = value.strip()
    if not normalized or normalized.casefold() in {"unknown", "none", "n/a", "-"}:
        raise ValueError(f"native {label} is unavailable")
    return normalized


def _required_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"native {label} must be boolean")
    return value


def _native_range(value: object, unit: str) -> CapabilityRange:
    if value is None:
        raise ValueError("native capability range is unavailable")
    minimum = getattr(value, "minimum", None)
    maximum = getattr(value, "maximum", None)
    if isinstance(minimum, bool) or isinstance(maximum, bool):
        raise ValueError("native capability range is invalid")
    try:
        lower = float(cast(str | bytes | bytearray | SupportsFloat | SupportsIndex, minimum))
        upper = float(cast(str | bytes | bytearray | SupportsFloat | SupportsIndex, maximum))
    except (TypeError, ValueError) as error:
        raise ValueError("native capability range is invalid") from error
    if not math.isfinite(lower) or not math.isfinite(upper):
        raise ValueError("native capability range is invalid")
    return CapabilityRange(lower, upper, unit)


def _native_ranges(values: object, unit: str) -> tuple[CapabilityRange, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError("native capability ranges are unavailable")
    result = tuple(_native_range(value, unit) for value in values)
    if not result:
        raise ValueError("native capability ranges are unavailable")
    return result


def _observe_rx_channel_count(native_module: Any, route: str, timeout_ms: int) -> int | None:
    topology_probe = getattr(native_module, "probe_pluto_receiver_topology", None)
    if not callable(topology_probe):
        return None
    try:
        topology = topology_probe(route, timeout_ms)
        element_ids = tuple(
            str(getattr(element, "id", "")).strip().casefold()
            for element in (getattr(topology, "input_scan_elements", ()) or ())
        )
    except Exception:
        return None
    if len(set(element_ids)) != len(element_ids):
        return None
    complete_pairs = sum(
        {in_phase, quadrature}.issubset(element_ids)
        for in_phase, quadrature in (("voltage0", "voltage1"), ("voltage2", "voltage3"))
    )
    return complete_pairs or None


__all__ = [
    "AD936X_LIBIIO_ADAPTER_ID",
    "Ad936xCapabilityObservation",
    "Ad936xCapabilityObservationError",
    "Ad936xLibiioCapabilityAdapter",
]
