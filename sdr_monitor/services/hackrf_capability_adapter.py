"""R11-D read-only HackRF capability mapping over an injected SDK port."""

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


HACKRF_LIBHACKRF_ADAPTER_ID = "native.libhackrf.hackrf_one.v1"
_GENERIC_FAILURE = "HackRF capability observation failed closed"


class HackrfBoardKind(StrEnum):
    HACKRF_ONE = "hackrf_one"


@dataclass(frozen=True, slots=True, repr=False)
class HackrfReadOnlyProbe:
    """Adapter-private values returned by a future concrete libhackrf port."""

    board_kind: HackrfBoardKind
    serial_words: tuple[int, int, int, int]
    firmware_version: str
    usb_api_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "board_kind", HackrfBoardKind(self.board_kind))
        words = tuple(self.serial_words)
        if len(words) != 4 or any(
            isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF
            for word in words
        ):
            raise ValueError("HackRF serial must contain four unsigned 32-bit words")
        firmware = self.firmware_version
        if not isinstance(firmware, str) or firmware != firmware.strip() or not firmware:
            raise ValueError("HackRF firmware version must be normalized text")
        if len(firmware) > 128 or any(token in firmware.casefold() for token in ("usb:", "ip:", "\\", "/")):
            raise ValueError("HackRF firmware version must not contain a route or path")
        if (
            isinstance(self.usb_api_version, bool)
            or not isinstance(self.usb_api_version, int)
            or not 0 <= self.usb_api_version <= 0xFFFF
        ):
            raise ValueError("HackRF USB API version must be an unsigned 16-bit value")
        object.__setattr__(self, "serial_words", words)


class HackrfReadOnlyProbePort(Protocol):
    """The only SDK surface admitted by R11-D."""

    def probe(self) -> HackrfReadOnlyProbe: ...

    def close(self) -> None: ...


class HackrfCapabilityObservationError(RuntimeError):
    """A redacted failure that never forwards an SDK/USB exception."""


@dataclass(frozen=True, slots=True)
class HackrfCapabilityObservation:
    snapshot: DeviceCapabilitySnapshot
    calibration_identity: DeviceCalibrationIdentity

    def __post_init__(self) -> None:
        if self.snapshot.family is not DeviceFamily.HACKRF:
            raise ValueError("HackRF observation requires the HackRF family")
        if self.calibration_identity.family is not self.snapshot.family:
            raise ValueError("calibration identity family must match the capability snapshot")
        if self.calibration_identity.adapter_id != self.snapshot.adapter_id:
            raise ValueError("calibration identity adapter must match the capability snapshot")
        if self.calibration_identity.device_identity_key != self.snapshot.identity_key:
            raise ValueError("calibration identity device key must match the capability snapshot")


class HackrfCapabilityAdapter:
    """Map one temporary read-only port; never expose stream or control calls."""

    def __init__(self, port_factory: Callable[[], HackrfReadOnlyProbePort]) -> None:
        if not callable(port_factory):
            raise ValueError("HackRF port factory must be callable")
        self._port_factory = port_factory

    def observe(self) -> HackrfCapabilityObservation:
        port: HackrfReadOnlyProbePort | None = None
        probe: HackrfReadOnlyProbe | None = None
        failed = False
        try:
            port = self._port_factory()
            probe = port.probe()
            if not isinstance(probe, HackrfReadOnlyProbe):
                failed = True
        except Exception:
            failed = True
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    failed = True
        if failed or probe is None:
            raise HackrfCapabilityObservationError(_GENERIC_FAILURE)

        try:
            if probe.board_kind is not HackrfBoardKind.HACKRF_ONE:
                raise ValueError("unsupported HackRF board kind")
            serial_identity = "|".join(f"{word:08x}" for word in probe.serial_words)
            identity_key = stable_identity_key(f"hackrf-one-serial|{serial_identity}")
            firmware_fingerprint = stable_identity_key(
                f"hackrf-one-firmware|{probe.firmware_version}|usb-api:{probe.usb_api_version:04x}"
            )
            snapshot = DeviceCapabilitySnapshot(
                device_id=f"hackrf-{identity_key[7:23]}",
                identity_key=identity_key,
                label="HackRF One receiver",
                family=DeviceFamily.HACKRF,
                adapter_id=HACKRF_LIBHACKRF_ADAPTER_ID,
                transports=(CapabilityTransport.USB,),
                acquisition_kinds=(AcquisitionKind.COMPLEX_IQ,),
                tuning_ranges_hz=(CapabilityRange(1e6, 6e9, "Hz"),),
                sample_rate_ranges_hz=(CapabilityRange(2e6, 20e6, "Hz"),),
                analog_bandwidth_ranges_hz=(),
                gain_ranges_db=(),
                rx_channel_count=1,
                shared_rx_lo=None,
                raw_iq_available=True,
                hardware_timestamp_available=None,
                hardware_overflow_counter_available=None,
                evidence=(
                    CapabilityEvidence(
                        CapabilityField.TRANSPORT,
                        CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY,
                        "libhackrf-device-list",
                    ),
                    CapabilityEvidence(
                        CapabilityField.ACQUISITION_KIND,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        "gsg-hackrf-one-spec",
                    ),
                    CapabilityEvidence(
                        CapabilityField.TUNING_RANGE,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        "gsg-hackrf-one-spec",
                    ),
                    CapabilityEvidence(
                        CapabilityField.SAMPLE_RATE_RANGE,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        "gsg-hackrf-one-spec",
                    ),
                    CapabilityEvidence(
                        CapabilityField.RX_CHANNEL_COUNT,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        "gsg-hackrf-one-spec",
                    ),
                    CapabilityEvidence(
                        CapabilityField.RAW_IQ,
                        CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                        "gsg-hackrf-one-spec",
                    ),
                ),
            )
            identity = DeviceCalibrationIdentity(
                family=DeviceFamily.HACKRF,
                adapter_id=HACKRF_LIBHACKRF_ADAPTER_ID,
                device_identity_key=identity_key,
                firmware_fingerprint=firmware_fingerprint,
            )
            return HackrfCapabilityObservation(snapshot, identity)
        except Exception:
            raise HackrfCapabilityObservationError(_GENERIC_FAILURE) from None


__all__ = [
    "HACKRF_LIBHACKRF_ADAPTER_ID",
    "HackrfBoardKind",
    "HackrfCapabilityAdapter",
    "HackrfCapabilityObservation",
    "HackrfCapabilityObservationError",
    "HackrfReadOnlyProbe",
    "HackrfReadOnlyProbePort",
]
