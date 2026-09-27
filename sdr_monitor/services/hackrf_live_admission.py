"""Pure capability-bound admission for a future explicit HackRF Live owner.

This module creates an immutable plan only.  It has no vendor runtime, device
factory or product-Live dependency, so validating an activation request cannot
open, configure or stream a receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..domain import (
    AcquisitionKind,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityTransport,
    DeviceCalibrationIdentity,
    DeviceCapabilitySnapshot,
    DeviceFamily,
)
from ..domain.hackrf_live import HackrfLiveRequest
from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID

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
