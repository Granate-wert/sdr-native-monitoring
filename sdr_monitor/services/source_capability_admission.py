"""No-I/O compatibility over the existing catalog and family request types.

This is NOT a Start permit, hardware readback, firmware/settings application,
or RF proof. The selected owner still revalidates identity and applies its
family-specific request. Never translate Pluto gain into HackRF gain stages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.device_capabilities import (
    AcquisitionKind,
    AdapterRuntimeAvailability,
    CapabilityRange,
    DeviceCapabilityInventory,
    DeviceCapabilitySnapshot,
    DeviceFamily,
)
from ..domain.live import LiveConfiguration
from ..domain.ad936x_route_capabilities import Ad936xRouteCapabilities
from ..domain.hackrf_sweep import HackrfSweepRequest
from .ad936x_capability_adapter import AD936X_LIBIIO_ADAPTER_ID
from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID
from .hackrf_live_admission import HackrfLiveAdmissionReason, HackrfLiveRequest, admit_hackrf_live
from .tinysa_capability_adapter import TINYSA_READ_ONLY_ADAPTER_ID
from .rtl_capability_provider import RTL_ADAPTER_ID


class SourceRequestAdmissionReason(StrEnum):
    SOURCE_NOT_FOUND = "source_not_found"
    IDENTITY_UNVERIFIED = "identity_unverified"
    RUNTIME_NOT_OBSERVED = "runtime_not_observed"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    REQUEST_MODE = "request_mode"
    REQUEST_TYPE = "request_type"
    REQUEST_SOURCE = "request_source"
    REQUEST_MODEL = "request_model"
    REQUEST_RANGE = "request_range"
    CONFIGURATION_REQUIRED = "configuration_required"
    CAPABILITY_UNVERIFIED = "capability_unverified"
    DEVICE_MODE_UNSUPPORTED = "device_mode_unsupported"
    MODE_RUNTIME_UNAVAILABLE = "mode_runtime_unavailable"
    FAMILY_NOT_INTEGRATED = "family_not_integrated"


@dataclass(frozen=True, slots=True)
class SourceRequestAdmission:
    reason: SourceRequestAdmissionReason | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


def live_configuration_numbers_valid(value: LiveConfiguration) -> bool:
    """Also protect explicit unverified-route compatibility before SDK open."""
    values = (value.center_hz, value.sample_rate_hz, value.gain_db)
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
               for item in values):
        return False
    bandwidth = value.analog_bandwidth_hz
    return (value.center_hz > 0 and value.sample_rate_hz > 0
            and (bandwidth is None or (isinstance(bandwidth, (int, float))
                                      and not isinstance(bandwidth, bool)
                                      and math.isfinite(bandwidth) and bandwidth > 0)))


def _contains(ranges: tuple[CapabilityRange, ...], minimum: float, maximum: float) -> bool:
    return any(item.minimum <= minimum <= maximum <= item.maximum for item in ranges)


def _ad936x_live_reason(snapshot: DeviceCapabilitySnapshot | Ad936xRouteCapabilities, request: LiveConfiguration) -> SourceRequestAdmissionReason | None:
    if not live_configuration_numbers_valid(request):
        return SourceRequestAdmissionReason.REQUEST_RANGE
    facts: tuple[tuple[tuple[CapabilityRange, ...], float], ...] = ((snapshot.tuning_ranges_hz, request.center_hz),
             (snapshot.sample_rate_ranges_hz, request.sample_rate_hz),
             (snapshot.gain_ranges_db, request.gain_db))
    if request.analog_bandwidth_hz is not None:
        facts += ((snapshot.analog_bandwidth_ranges_hz, request.analog_bandwidth_hz),)
    for ranges, value in facts:
        if not ranges:
            return SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED
        if not _contains(ranges, value, value):
            return SourceRequestAdmissionReason.REQUEST_RANGE
    return None


def admit_ad936x_route_request(
    facts: Ad936xRouteCapabilities, request: LiveConfiguration | ContinuousSweepPlanRequest,
    *, applied_live: LiveConfiguration | None = None,
) -> SourceRequestAdmission:
    """Pure bounds only; caller MUST bind exact selected descriptor/runtime.

    This does not admit a stable identity, catalog join, calibration or Start.
    All RF and non-frequency fields still pass the existing native preflight.
    """
    if not isinstance(facts, Ad936xRouteCapabilities):
        return SourceRequestAdmission(SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED)
    try:
        facts.__post_init__()
        request.__post_init__()
    except (TypeError, ValueError, AttributeError):
        return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
    if isinstance(request, LiveConfiguration):
        return SourceRequestAdmission(_ad936x_live_reason(facts, request))
    if not isinstance(request, ContinuousSweepPlanRequest):
        return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
    if not isinstance(applied_live, LiveConfiguration):
        return SourceRequestAdmission(SourceRequestAdmissionReason.CONFIGURATION_REQUIRED)
    reason = _ad936x_live_reason(facts, applied_live)
    if reason is not None:
        return SourceRequestAdmission(reason)
    bandwidth = applied_live.analog_bandwidth_hz
    window = min(applied_live.sample_rate_hz, bandwidth) if bandwidth is not None else applied_live.sample_rate_hz
    if (not _contains(facts.tuning_ranges_hz, request.start_hz, request.stop_hz)
            or request.usable_window_hz > window):
        return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
    return SourceRequestAdmission()


def admit_source_request(
    inventory: DeviceCapabilityInventory, source_id: str, mode: str, request: object,
    *, applied_live: LiveConfiguration | None = None,
    hackrf_sweep_runtime_available: bool = False,
) -> SourceRequestAdmission:
    """Pure typed compatibility. Missing evidence never becomes unsupported.

    TinySA settings/current input and SDK ownership remain subsequent gates.
    HackRF Sweep needs an explicitly composed and versioned runtime. The
    default remains unavailable so old/SDK-OFF modules cannot gain the route.
    """
    try:
        binding = inventory.binding_for_source(source_id)
    except (TypeError, ValueError):
        binding = None
    if binding is None:
        return SourceRequestAdmission(SourceRequestAdmissionReason.SOURCE_NOT_FOUND)
    if not isinstance(mode, str) or mode not in {"rtbw", "sweep"}:
        return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_MODE)
    if binding.family is DeviceFamily.RTL_SDR and binding.adapter_id == RTL_ADAPTER_ID:
        if mode != "rtbw":
            return SourceRequestAdmission(SourceRequestAdmissionReason.DEVICE_MODE_UNSUPPORTED)
        from ..domain.rtl_live import RtlLiveRequest
        if not isinstance(request, RtlLiveRequest):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
        if request.source_id != binding.source_id:
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_SOURCE)
        route = binding.rtl_session_route
        if route is None or route.normal_tuner_path is not True:
            return SourceRequestAdmission(SourceRequestAdmissionReason.IDENTITY_UNVERIFIED)
        runtime = inventory.runtime_for_adapter(binding.adapter_id)
        if runtime is None or runtime.availability is AdapterRuntimeAvailability.UNKNOWN:
            return SourceRequestAdmission(SourceRequestAdmissionReason.RUNTIME_NOT_OBSERVED)
        if runtime.availability is not AdapterRuntimeAvailability.AVAILABLE:
            return SourceRequestAdmission(SourceRequestAdmissionReason.RUNTIME_UNAVAILABLE)
        try:
            request.__post_init__()
        except (TypeError, ValueError):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
        # No stable serial/calibration or tuner-specific RF range is inferred.
        # The native owner must recheck one current route, tuner mode and the
        # exact center/Fs setters' readbacks before admitting any IQ callback.
        return SourceRequestAdmission()
    snapshot = binding.snapshot
    identity = binding.calibration_identity
    if snapshot is None or identity is None:
        return SourceRequestAdmission(SourceRequestAdmissionReason.IDENTITY_UNVERIFIED)
    runtime = inventory.runtime_for_adapter(binding.adapter_id)
    if runtime is None or runtime.availability is AdapterRuntimeAvailability.UNKNOWN:
        return SourceRequestAdmission(SourceRequestAdmissionReason.RUNTIME_NOT_OBSERVED)
    if runtime.availability is AdapterRuntimeAvailability.UNAVAILABLE:
        return SourceRequestAdmission(SourceRequestAdmissionReason.RUNTIME_UNAVAILABLE)
    if snapshot.family is DeviceFamily.AD936X and snapshot.adapter_id == AD936X_LIBIIO_ADAPTER_ID:
        if AcquisitionKind.COMPLEX_IQ not in snapshot.acquisition_kinds or snapshot.raw_iq_available is not True:
            return SourceRequestAdmission(SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED)
        if mode == "rtbw":
            if not isinstance(request, LiveConfiguration):
                return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
            return SourceRequestAdmission(_ad936x_live_reason(snapshot, request))
        if not isinstance(request, ContinuousSweepPlanRequest):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
        if not isinstance(applied_live, LiveConfiguration):
            return SourceRequestAdmission(SourceRequestAdmissionReason.CONFIGURATION_REQUIRED)
        reason = _ad936x_live_reason(snapshot, applied_live)
        if reason is not None:
            return SourceRequestAdmission(reason)
        bandwidth = applied_live.analog_bandwidth_hz
        admitted_window = min(applied_live.sample_rate_hz, bandwidth) if bandwidth is not None else applied_live.sample_rate_hz
        if (not _contains(snapshot.tuning_ranges_hz, request.start_hz, request.stop_hz)
                or request.usable_window_hz > admitted_window):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
        return SourceRequestAdmission()
    if snapshot.family is DeviceFamily.HACKRF and snapshot.adapter_id == HACKRF_LIBHACKRF_ADAPTER_ID:
        if mode == "sweep":
            if not hackrf_sweep_runtime_available:
                return SourceRequestAdmission(SourceRequestAdmissionReason.MODE_RUNTIME_UNAVAILABLE)
            if not isinstance(request, HackrfSweepRequest):
                return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
            if request.source.device_id != source_id or request.source.binding is not binding:
                return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_SOURCE)
            if (snapshot.model_id != "hackrf_one" or snapshot.raw_iq_available is not True
                    or AcquisitionKind.COMPLEX_IQ not in snapshot.acquisition_kinds
                    or not snapshot.tuning_ranges_hz):
                return SourceRequestAdmission(SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED)
            try:
                request.__post_init__()
            except (TypeError, ValueError):
                return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
            if (not _contains(snapshot.tuning_ranges_hz, request.start_hz, request.stop_hz)
                    or not _contains(snapshot.tuning_ranges_hz, request.start_hz + 7_500_000,
                                     request.hardware_stop_hz - 7_500_000)):
                return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
            return SourceRequestAdmission()
        if not isinstance(request, HackrfLiveRequest):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
        if request.source_id != binding.source_id:
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_SOURCE)
        if not snapshot.tuning_ranges_hz or not snapshot.sample_rate_ranges_hz:
            return SourceRequestAdmission(SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED)
        admitted = admit_hackrf_live(snapshot, identity, request)
        if admitted.accepted:
            return SourceRequestAdmission()
        if admitted.reason in {HackrfLiveAdmissionReason.CENTER_FREQUENCY, HackrfLiveAdmissionReason.SAMPLE_RATE}:
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
        if admitted.reason is HackrfLiveAdmissionReason.IDENTITY:
            return SourceRequestAdmission(SourceRequestAdmissionReason.IDENTITY_UNVERIFIED)
        return SourceRequestAdmission(SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED)
    if snapshot.family is DeviceFamily.TINYSA and snapshot.adapter_id == TINYSA_READ_ONLY_ADAPTER_ID:
        if mode == "rtbw":
            return SourceRequestAdmission(SourceRequestAdmissionReason.DEVICE_MODE_UNSUPPORTED)
        # Keep serial/trace dependencies out of the Pluto-only import path.
        # Importing the request type opens no port and issues no measurement.
        from .tinysa_serial_trace_collector import TinySaScanRawRequest

        if not isinstance(request, TinySaScanRawRequest):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_TYPE)
        if (snapshot.model_id is None or not snapshot.tuning_ranges_hz
                or snapshot.acquisition_kinds != (AcquisitionKind.SPECTRUM_TRACE,)
                or snapshot.raw_iq_available is not False):
            return SourceRequestAdmission(SourceRequestAdmissionReason.CAPABILITY_UNVERIFIED)
        if request.model.value != snapshot.model_id:
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_MODEL)
        if not _contains(snapshot.tuning_ranges_hz, request.start_frequency_hz, request.stop_frequency_hz):
            return SourceRequestAdmission(SourceRequestAdmissionReason.REQUEST_RANGE)
        return SourceRequestAdmission()
    return SourceRequestAdmission(SourceRequestAdmissionReason.FAMILY_NOT_INTEGRATED)


__all__ = [
    "SourceRequestAdmission",
    "SourceRequestAdmissionReason",
    "admit_source_request",
    "admit_ad936x_route_request",
    "live_configuration_numbers_valid",
]
