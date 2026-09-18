"""Pure capability-bound admission for a future explicit HackRF Live owner.

This module creates an immutable plan only.  It has no vendor runtime, device
factory or product-Live dependency, so validating an activation request cannot
open, configure or stream a receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from numbers import Real

from ..domain import (
    AcquisitionKind,
    BackendKind,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityTransport,
    DeviceCalibrationIdentity,
    DeviceCapabilitySnapshot,
    DeviceFamily,
    SourceId,
    as_source_id,
)
from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID


_HACKRF_FILTER_BANDWIDTHS_HZ = frozenset(
    (
        1_750_000,
        2_500_000,
        3_500_000,
        5_000_000,
        5_500_000,
        6_000_000,
        7_000_000,
        8_000_000,
        9_000_000,
        10_000_000,
        12_000_000,
        14_000_000,
        15_000_000,
        20_000_000,
        24_000_000,
        28_000_000,
    )
)
_WINDOWS = frozenset(
    (
        "rectangular",
        "hann",
        "blackman_harris_4term",
        "flat_top",
        "nuttall",
        "kaiser",
    )
)
_DETECTORS = frozenset(("sample", "peak", "negative_peak", "rms", "average_power"))
_MAX_QUEUE_CAPACITY = 64
# The qualified official runtime supplies 262144-byte CI8 transfers; the native
# pipeline drains CPU output after each transfer. A different runtime transfer
# size needs requalification against its reported source.slot_bytes. This is
# a default for the qualified runtime, not an inferred hardware guarantee.
# Keep the analytical burst buffer distinct from
# the small freshest-window presentation queue. These are bounded scalar
# policy values, not runtime discovery or a process-RSS guarantee.
_TRANSFER_SAMPLES = 262_144 // 2
_MAX_DSP_OUTPUT_CAPACITY = 4096
_SPECTRUM_QUEUE_BUDGET_BYTES = 64 * 1024 * 1024
_SPECTRUM_FRAME_OVERHEAD_ALLOWANCE_BYTES = 1024
_MAX_GENERATION = (1 << 63) - 1
_PLAN_ISSUER = object()


class HackrfLiveAdmissionReason(StrEnum):
    """Finite, redacted refusal vocabulary for the future composition root."""

    DEVICE_FAMILY = "device_family"
    ADAPTER = "adapter"
    ACQUISITION_KIND = "acquisition_kind"
    RAW_IQ = "raw_iq"
    TRANSPORT_PROVENANCE = "transport_provenance"
    IDENTITY = "identity"
    CENTER_FREQUENCY = "center_frequency"
    SAMPLE_RATE = "sample_rate"


def _finite_positive(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be finite and positive")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{label} must be finite and positive")
    return normalized


def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _opaque_source_id(value: object) -> SourceId:
    source_id = as_source_id(value)
    if len(source_id) > 128 or any(token in source_id.casefold() for token in ("usb:", "ip:", "\\", "/")):
        raise ValueError("source_id must be a route-free opaque identifier")
    return source_id


@dataclass(frozen=True, slots=True)
class HackrfLiveRequest:
    """Complete no-side-effect RX/DSP request for one future HackRF owner."""

    center_frequency_hz: float
    sample_rate_hz: float
    baseband_filter_hz: int
    lna_gain_db: int
    vga_gain_db: int
    rf_amplifier_enabled: bool = False
    bias_tee_enabled: bool = False
    fft_size: int = 4096
    hop_size: int = 2048
    window: str = "hann"
    detector: str = "sample"
    backend: BackendKind = BackendKind.CPU
    persistence_enabled: bool = False
    slot_count: int = 32
    ready_capacity: int = 24
    # None selects enough outputs for one transfer, including FFT overlap
    # carried from the preceding transfer. Keep the automatic policy as None
    # so dataclasses.replace(..., fft_size=..., hop_size=...) recalculates it.
    # Explicit smaller queues remain
    # available for bounded lossy/diagnostic profiles and are never increased.
    dsp_output_capacity: int | None = None
    presentation_capacity: int = 4
    configuration_generation: int = 1
    source_id: SourceId = SourceId("native.hackrf.live")

    def __post_init__(self) -> None:
        object.__setattr__(self, "center_frequency_hz", _finite_positive(self.center_frequency_hz, "center_frequency_hz"))
        object.__setattr__(self, "sample_rate_hz", _finite_positive(self.sample_rate_hz, "sample_rate_hz"))
        if (
            isinstance(self.baseband_filter_hz, bool)
            or not isinstance(self.baseband_filter_hz, int)
            or self.baseband_filter_hz not in _HACKRF_FILTER_BANDWIDTHS_HZ
        ):
            raise ValueError("baseband_filter_hz must be a documented HackRF setting")
        if self.baseband_filter_hz > self.sample_rate_hz:
            raise ValueError("baseband_filter_hz must not exceed sample_rate_hz")
        if (
            isinstance(self.lna_gain_db, bool)
            or not isinstance(self.lna_gain_db, int)
            or not 0 <= self.lna_gain_db <= 40
            or self.lna_gain_db % 8 != 0
        ):
            raise ValueError("lna_gain_db must be a HackRF 0..40 dB step of 8")
        if (
            isinstance(self.vga_gain_db, bool)
            or not isinstance(self.vga_gain_db, int)
            or not 0 <= self.vga_gain_db <= 62
            or self.vga_gain_db % 2 != 0
        ):
            raise ValueError("vga_gain_db must be a HackRF 0..62 dB step of 2")
        if self.rf_amplifier_enabled or self.bias_tee_enabled:
            raise ValueError("R11-M requires RF amplifier and bias tee disabled")
        fft_size = _bounded_integer(self.fft_size, "fft_size", 256, 262_144)
        if fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two")
        object.__setattr__(self, "fft_size", fft_size)
        hop_size = _bounded_integer(self.hop_size, "hop_size", 1, fft_size)
        object.__setattr__(self, "hop_size", hop_size)
        window = self.window.strip().casefold().replace("-", "_") if isinstance(self.window, str) else ""
        detector = self.detector.strip().casefold().replace("-", "_") if isinstance(self.detector, str) else ""
        if window not in _WINDOWS:
            raise ValueError("window must be a canonical CPU DSP window")
        if detector not in _DETECTORS:
            raise ValueError("detector must be a canonical CPU DSP detector")
        object.__setattr__(self, "window", window)
        object.__setattr__(self, "detector", detector)
        if BackendKind(self.backend) is not BackendKind.CPU:
            raise ValueError("R11-M admits only the CPU DSP backend")
        object.__setattr__(self, "backend", BackendKind.CPU)
        if self.persistence_enabled:
            raise ValueError("R11-M does not admit persistence")
        slot_count = _bounded_integer(self.slot_count, "slot_count", 1, _MAX_QUEUE_CAPACITY)
        ready_capacity = _bounded_integer(self.ready_capacity, "ready_capacity", 1, slot_count)
        object.__setattr__(self, "slot_count", slot_count)
        object.__setattr__(self, "ready_capacity", ready_capacity)
        output_capacity = self.dsp_output_capacity
        if output_capacity is None:
            output_capacity = (_TRANSFER_SAMPLES + hop_size - 1) // hop_size
        output_capacity = _bounded_integer(
            output_capacity, "dsp_output_capacity", 1, _MAX_DSP_OUTPUT_CAPACITY)
        presentation_capacity = _bounded_integer(
            self.presentation_capacity, "presentation_capacity", 1, _MAX_QUEUE_CAPACITY)
        # Worst-case independent float64 frequencies + float32 values per frame;
        # sharing a frequency grid may reduce actual memory, never raise the cap.
        frame_bytes = fft_size * 12 + _SPECTRUM_FRAME_OVERHEAD_ALLOWANCE_BYTES
        if (output_capacity + presentation_capacity) * frame_bytes > _SPECTRUM_QUEUE_BUDGET_BYTES:
            raise ValueError("HackRF spectrum queues exceed the 64 MiB allocation policy")
        object.__setattr__(self, "presentation_capacity", presentation_capacity)
        object.__setattr__(self, "configuration_generation", _bounded_integer(self.configuration_generation, "configuration_generation", 1, _MAX_GENERATION))
        object.__setattr__(self, "source_id", _opaque_source_id(self.source_id))

    @property
    def resolved_dsp_output_capacity(self) -> int:
        """Validated, deterministic native value; no runtime lookup or allocation."""
        if self.dsp_output_capacity is not None:
            return self.dsp_output_capacity
        return (_TRANSFER_SAMPLES + self.hop_size - 1) // self.hop_size


@dataclass(frozen=True, slots=True, init=False)
class HackrfLiveActivationPlan:
    """Complete, route-free input issued only by successful admission."""

    device_id: str
    identity_key: str
    adapter_id: str
    request: HackrfLiveRequest
    _issuer: object = field(repr=False, compare=False)

    @classmethod
    def _issue(
        cls,
        *,
        device_id: str,
        identity_key: str,
        adapter_id: str,
        request: HackrfLiveRequest,
    ) -> HackrfLiveActivationPlan:
        result = object.__new__(cls)
        object.__setattr__(result, "device_id", device_id)
        object.__setattr__(result, "identity_key", identity_key)
        object.__setattr__(result, "adapter_id", adapter_id)
        object.__setattr__(result, "request", request)
        object.__setattr__(result, "_issuer", _PLAN_ISSUER)
        return result


@dataclass(frozen=True, slots=True)
class HackrfLiveAdmission:
    """Either one complete plan or one refusal; both are side-effect free."""

    plan: HackrfLiveActivationPlan | None = None
    reason: HackrfLiveAdmissionReason | None = None

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.reason is None):
            raise ValueError("admission must contain exactly one plan or refusal reason")

    @property
    def accepted(self) -> bool:
        return self.plan is not None


def _contains(ranges: tuple[object, ...], value: float) -> bool:
    return any(
        float(getattr(candidate, "minimum")) <= value <= float(getattr(candidate, "maximum"))
        for candidate in ranges
    )


def _reject(reason: HackrfLiveAdmissionReason) -> HackrfLiveAdmission:
    return HackrfLiveAdmission(reason=reason)


def _is_issued_hackrf_live_activation_plan(value: object) -> bool:
    """Internal provenance check for the subsequent explicit factory layer."""

    return (
        isinstance(value, HackrfLiveActivationPlan)
        and getattr(value, "_issuer", None) is _PLAN_ISSUER
        and isinstance(value.request, HackrfLiveRequest)
    )


def admit_hackrf_live(
    snapshot: DeviceCapabilitySnapshot,
    identity: DeviceCalibrationIdentity,
    request: HackrfLiveRequest,
) -> HackrfLiveAdmission:
    """Return an auditable plan without accessing a runtime or device."""

    if snapshot.family is not DeviceFamily.HACKRF:
        return _reject(HackrfLiveAdmissionReason.DEVICE_FAMILY)
    if snapshot.adapter_id != HACKRF_LIBHACKRF_ADAPTER_ID:
        return _reject(HackrfLiveAdmissionReason.ADAPTER)
    if snapshot.acquisition_kinds != (AcquisitionKind.COMPLEX_IQ,) or snapshot.rx_channel_count != 1:
        return _reject(HackrfLiveAdmissionReason.ACQUISITION_KIND)
    if snapshot.raw_iq_available is not True:
        return _reject(HackrfLiveAdmissionReason.RAW_IQ)
    transport = snapshot.evidence_for(CapabilityField.TRANSPORT)
    if (
        CapabilityTransport.USB not in snapshot.transports
        or transport.origin is not CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY
    ):
        return _reject(HackrfLiveAdmissionReason.TRANSPORT_PROVENANCE)
    if (
        identity.family is not snapshot.family
        or identity.adapter_id != snapshot.adapter_id
        or identity.device_identity_key != snapshot.identity_key
    ):
        return _reject(HackrfLiveAdmissionReason.IDENTITY)
    if not _contains(snapshot.tuning_ranges_hz, request.center_frequency_hz):
        return _reject(HackrfLiveAdmissionReason.CENTER_FREQUENCY)
    if not _contains(snapshot.sample_rate_ranges_hz, request.sample_rate_hz):
        return _reject(HackrfLiveAdmissionReason.SAMPLE_RATE)
    return HackrfLiveAdmission(
        plan=HackrfLiveActivationPlan._issue(
            device_id=snapshot.device_id,
            identity_key=snapshot.identity_key,
            adapter_id=snapshot.adapter_id,
            request=request,
        )
    )


__all__ = [
    "HackrfLiveActivationPlan",
    "HackrfLiveAdmission",
    "HackrfLiveAdmissionReason",
    "HackrfLiveRequest",
    "admit_hackrf_live",
]
