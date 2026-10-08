"""HF/RTL pure admission and bounded SAME owner join; no SDK or numerical DSP."""

from collections import OrderedDict
from hashlib import sha256
from struct import pack
from typing import Any

from ..domain.analytical_journal import JournalState, OwnerJournalSnapshot
from ..domain.analytical_ready import DetectorReadyReceipt
from ..domain.live import BackendKind, LiveAdmissionRejected, LiveSessionState, LiveSnapshot
from ..domain.hackrf_live import HackrfLiveRequest
from ..domain.processing_policy import HostDcMode, HostSpurMode, SdrProcessingPolicyV1
from ..domain.source_processing import (
    AppliedSourceProcessingContextV2, RfProcessingValue, RfValueAuthority, SourceProcessingFrameKeyV2,
)
from ..domain.spectrum_provenance import SpectrumProvenance


def admit_source_processing(native: object, family: str, policy: SdrProcessingPolicyV1,
                            *, backend: BackendKind = BackendKind.CPU) -> bool:
    """Exact loaded-code handshake before permit/claim/open; legacy OFF unchanged."""
    if type(policy) is not SdrProcessingPolicyV1 or family not in ("hackrf", "rtl_sdr"):
        raise LiveAdmissionRejected("typed family processing request required")
    if policy.is_off:
        return False
    if (policy.spur_mode is not HostSpurMode.OFF or policy.compare_raw
            or backend is not BackendKind.CPU):
        raise LiveAdmissionRejected("family processing requires CPU BlockMean; spur/comparison unsupported")
    getter = getattr(native, "hackrf_processing_factory_contract" if family == "hackrf"
                     else "rtl_processing_factory_contract", None)
    contract = getter() if callable(getter) else None
    expected = {"schema_version": 1, "family": "hackrf" if family == "hackrf" else "rtl-sdr",
                "scope": "native_rtbw_factory_only", "dc_modes": ("off", "block_mean_v1"),
                "argument": "dc_removal_block_mean", "default_off": True, "full_owner_context": False}
    if (type(contract) is not dict or set(contract) != set(expected)
            or any(type(contract[k]) is not type(v) or contract[k] != v for k, v in expected.items())):
        raise LiveAdmissionRejected("family native processing factory contract unavailable or incompatible")
    return policy.dc_mode is HostDcMode.BLOCK_MEAN


class SourceProcessingJoin:
    """Four-grid per-owner cache; source identity does not claim physical aliasing."""

    def __init__(self) -> None:
        self._grids: OrderedDict[tuple[object, ...], str] = OrderedDict()

    def clear(self) -> None:
        self._grids.clear()

    def receipt(self, family: str, frame: Any, current: LiveSnapshot,
                provenance: SpectrumProvenance, unit: str,
                ready: DetectorReadyReceipt | None,
                journal: OwnerJournalSnapshot) -> AppliedSourceProcessingContextV2 | None:
        if family not in ("hackrf", "rtl_sdr"):
            raise ValueError("unsupported processing owner family")
        if type(current) is not LiveSnapshot:
            return None  # Legacy conversion-only fixtures have no admitted owner authority.
        request = current.hackrf_request if family == "hackrf" else current.rtl_request
        if request is None:
            raise ValueError("family owner request missing")
        policy = request.processing_policy
        scope, counters, recipe, choice = journal.scope, journal.counters, provenance.processing_recipe, current.source_choice
        complete = (current.state is LiveSessionState.RUNNING and choice is not None
            and choice.family.value == family and ready is not None and ready.owner_run_id is not None
            and scope is not None and counters is not None and recipe is not None
            and journal.state is JournalState.ACTIVE)
        if not complete:
            if not policy.is_off:
                raise ValueError("processed family frame lacks authenticated SAME owner context")
            return None
        assert scope is not None and counters is not None and recipe is not None and choice is not None
        assert ready is not None
        if (current.active_source_id != request.source_id
                or current.active_config_generation != request.configuration_generation
                or frame.source.source_id != request.source_id
                or type(frame.config_generation) is not int
                or frame.config_generation != request.configuration_generation
                or ready.source_id != request.source_id or ready.receiver_id is not None
                or ready.config_generation != request.configuration_generation
                or ready.acquisition_epoch != current.acquisition_epoch
                or ready.session_id != current.session_id
                or ready.owner_run_id != scope.owner_run_id
                or ready.producer_instance_id != counters.producer_instance_id
                or ready.adapter_clock_scope_id != scope.clock_scope_id
                or ready.host_process_id != scope.host_process_id
                or scope.source_id != request.source_id or scope.receiver_id is not None
                or scope.configuration_generation != request.configuration_generation
                or scope.acquisition_epoch != current.acquisition_epoch or scope.session_id != current.session_id
                or frame.center_frequency_hz != request.center_frequency_hz
                or frame.sample_rate_hz != request.sample_rate_hz
                or type(frame.fft_size) is not int or type(frame.hop_size) is not int
                or frame.fft_size != request.fft_size or frame.hop_size != request.hop_size
                or frame.source.backend_id != ("native.libhackrf.rx.v1" if family == "hackrf"
                    else "native.librtlsdr.unbundled.cpu.v1")
                or unit != "dBFS/bin" or request.backend is not BackendKind.CPU
                or provenance.window_normalization_version != "power-norm-v1"
                or provenance.precision_mode != "reference_f64" or provenance.calibration_status != "uncalibrated"
                or provenance.window != request.window or provenance.detector != request.detector
                or provenance.averaging_frames != getattr(request, "averaging_frames", 1)
                or recipe.policy != policy):
            raise ValueError("family processing frame does not belong to admitted owner/policy")
        center, rate = float(request.center_frequency_hz), float(request.sample_rate_hz)
        size = request.fft_size
        if (len(frame.frequencies_hz) != size or len(frame.values) != size
                or float(frame.frequencies_hz[0]) != center - rate / 2
                or float(frame.frequencies_hz[-1]) != center + rate / 2 - rate / size):
            raise ValueError("family processing FFT grid differs from admitted geometry")
        grid = (center, rate, size, unit, "power-norm-v1")
        digest = self._grids.get(grid)
        if digest is None:
            digest = "sha256:" + sha256(b"regular-fftshift-grid-v1\0" + pack("!ddQ", center, rate, size)
                + b"dBFS/bin\0power-norm-v1").hexdigest()
            self._grids[grid] = digest
            if len(self._grids) > 4:
                self._grids.popitem(last=False)
        authority = RfValueAuthority.SDK_APPLIED if family == "hackrf" else RfValueAuthority.READBACK
        if family == "hackrf":
            if not isinstance(request, HackrfLiveRequest):
                raise ValueError("HackRF owner lacks typed request")
            bandwidth = RfProcessingValue(float(request.baseband_filter_hz), RfValueAuthority.SDK_APPLIED)
        else:
            bandwidth = RfProcessingValue(None, RfValueAuthority.UNKNOWN)
        key = SourceProcessingFrameKeyV2(family, str(choice.device_id), str(request.source_id),
            str(current.session_id), scope.owner_run_id, counters.producer_instance_id,
            request.configuration_generation, current.acquisition_epoch, digest, unit,
            "power-norm-v1", "cpu-pocketfft", RfProcessingValue(center, authority),
            RfProcessingValue(rate, authority), bandwidth)
        return AppliedSourceProcessingContextV2(key, recipe.policy_digest, request.configuration_generation,
            recipe.dc_mode, recipe.whole_frame_modified, frame.quality_flags)
