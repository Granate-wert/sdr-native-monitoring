"""AD native policy admission and bounded HOST owner/recipe join; no SDK or IQ.

The native recipe proves the operation. Current RUNNING owner state supplies
resource/RX/RF/generation/host epoch. Neither replaces the other's authority.
Regular-grid digests identify the CPU FFT geometry, not raw axis-byte checksums.
"""

from collections import OrderedDict
from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from struct import pack
from typing import Any

from ..domain.live import BackendKind, LiveAdmissionRejected, LiveConfiguration, LiveSessionState, LiveSnapshot
from ..domain.analytical_ready import DetectorReadyReceipt
from ..domain.processing_policy import (
    AppliedProcessingContextV1, HostDcMode, HostSpurMode, ProcessingFrameKey,
)
from ..domain.receiver_topology import ReceiverChain, ReceiverChainSelection
from ..domain.spectrum_provenance import SpectrumProvenance
from .density_processing import admit_density_processing

_AD_WINDOWS = {"rectangular": "RECTANGULAR", "hann": "HANN", "hanning": "HANN",
    "blackman-harris": "BLACKMAN_HARRIS_4TERM", "blackmanharris": "BLACKMAN_HARRIS_4TERM",
    "flattop": "FLAT_TOP", "flat-top": "FLAT_TOP", "nuttall": "NUTTALL", "kaiser": "KAISER"}
_AD_DETECTORS = {"sample": "SAMPLE", "peak": "PEAK", "positive-peak": "PEAK",
    "negative-peak": "NEGATIVE_PEAK", "rms": "RMS", "average": "AVERAGE_POWER", "average-power": "AVERAGE_POWER"}


def ad_window_enum_name(value: str, *, strict: bool = False) -> str:
    name = (value or "hann").strip().casefold().replace("_", "-").replace(" ", "-")
    if strict and name not in _AD_WINDOWS:
        raise LiveAdmissionRejected("AD processed window has no exact native mapping")
    return _AD_WINDOWS.get(name, "HANN")


def ad_detector_enum_name(value: str, *, strict: bool = False) -> str:
    name = (value or "sample").strip().casefold().replace("_", "-")
    if strict and name not in _AD_DETECTORS:
        raise LiveAdmissionRejected("AD processed detector has no exact native mapping")
    return _AD_DETECTORS.get(name, "SAMPLE")


def admit_ad_processing(native: Any, configuration: LiveConfiguration) -> bool:
    """Pure loaded-code handshake BEFORE any owner/RF effect; OFF is compatible."""
    policy = configuration.processing_policy
    if policy.is_off:
        return False
    if (policy.spur_mode is not HostSpurMode.OFF or policy.compare_raw
            or configuration.backend is not BackendKind.CPU):
        raise LiveAdmissionRejected("AD processing requires explicit CPU BlockMean; spur/comparison unsupported")
    handshake = getattr(native, "dsp_processing_contract", None)
    contract = handshake() if callable(handshake) else None
    expected = {"schema_version": 1, "policy_schema_version": 1, "scope": "dsp_recipe_only",
        "full_owner_context": False, "producer_backends": ("cpu-pocketfft",),
        "dc_modes": ("off", "block_mean_v1"), "spur_modes": ("off",),
        "comparison": False, "canonical_input_required": True, "max_policy_bytes": 16384}
    if not isinstance(contract, Mapping) or any(
            type(contract.get(key)) is not type(value) or contract[key] != value
            for key, value in expected.items()):
        raise LiveAdmissionRejected("AD native processing recipe contract unavailable or incompatible")
    for kind, name in (("WindowType", ad_window_enum_name(configuration.window, strict=True)),
                       ("DetectorType", ad_detector_enum_name(configuration.detector, strict=True))):
        if getattr(getattr(native, kind, None), name, None) is None:
            raise LiveAdmissionRejected("AD processed DSP enum unavailable; silent substitution refused")
    admit_density_processing(native, policy, enabled=configuration.persistence_enabled)
    return policy.dc_mode is HostDcMode.BLOCK_MEAN


def require_ad_processing_topology(current: LiveSnapshot) -> None:
    topology = current.device.capabilities.receiver_topology if current.device is not None else None
    if topology is None or not topology.supports_selection(ReceiverChainSelection.RX1):
        raise LiveAdmissionRejected("AD processing requires observed receiver/resource topology")
    assert current.device is not None
    for identity in (topology.physical_stream_resource_id, current.device.device_id):
        if (type(identity) is not str or not identity or identity != identity.strip() or len(identity) > 128
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in identity)):
            raise LiveAdmissionRejected("AD processing requires bounded exact owner identities")


class LiveProcessingJoin:
    """One-owner bounded cache (at most four grid keys), never a global registry."""

    def __init__(self) -> None:
        self._grids: OrderedDict[tuple[object, ...], str] = OrderedDict()

    def clear(self) -> None:
        self._grids.clear()

    def receipt(self, frame: Any, current: LiveSnapshot, provenance: SpectrumProvenance,
                unit: str, source: str, receiver: str | None, revision: int,
                *, detector_ready: DetectorReadyReceipt | None,
                paired_sources: tuple[str, str] | None = None) -> AppliedProcessingContextV1 | None:
        applied = current.applied
        if applied is None:
            return None  # Legacy conversion-only callers have no owner authority.
        policy = applied.applied.processing_policy
        required = not policy.is_off
        device = current.device
        topology = device.capabilities.receiver_topology if device is not None else None
        actual = applied.applied
        complete = (current.state is LiveSessionState.RUNNING and topology is not None
            and {"center_hz", "sample_rate_hz", "analog_bandwidth_hz"}.issubset(applied.readback_fields)
            and actual.backend is BackendKind.CPU and receiver in ("RX1", "RX2")
            and type(current.acquisition_epoch) is int and current.acquisition_epoch > 0
            and type(current.active_config_generation) is int and current.active_config_generation > 0
            and provenance.window_normalization_version == "power-norm-v1"
            and provenance.processing_recipe is not None
            and isinstance(detector_ready, DetectorReadyReceipt) and detector_ready.owner_run_id is not None)
        if not complete:
            if required:
                raise ValueError("processed spectrum lacks complete SAME owner/native context")
            return None
        assert topology is not None and provenance.processing_recipe is not None and receiver is not None
        assert detector_ready is not None
        assert current.active_config_generation is not None and current.acquisition_epoch is not None
        expected_source = (paired_sources[0 if receiver == "RX1" else 1]
            if paired_sources is not None else current.active_source_id)
        chain = ReceiverChain[receiver]
        selection = ReceiverChainSelection(chain.value)
        hop = max(1, int(round(actual.fft_size * (1.0 - actual.overlap_ratio))))
        if (source != expected_source or frame.source.source_id != expected_source
                or type(frame.config_generation) is not int
                or frame.config_generation != current.active_config_generation
                or detector_ready.source_id != source or detector_ready.receiver_id != receiver
                or detector_ready.config_generation != current.active_config_generation
                or detector_ready.acquisition_epoch != current.acquisition_epoch
                or detector_ready.session_id != current.session_id
                or (paired_sources is None and receiver != current.receiver_id)
                or not topology.supports_selection(selection)
                or frame.center_frequency_hz != actual.center_hz or frame.sample_rate_hz != actual.sample_rate_hz
                or frame.fft_size != actual.fft_size or frame.hop_size != hop
                or provenance.window != ad_window_enum_name(actual.window).lower()
                or provenance.detector != ad_detector_enum_name(actual.detector).lower()
                or provenance.averaging_frames != actual.averaging_frames
                or provenance.processing_recipe.policy != policy
                or unit != "dBFS/bin" or actual.analog_bandwidth_hz is None
                or type(revision) is not int or not 1 <= revision < (1 << 64)):
            raise ValueError("processing recipe/frame does not belong to this admitted AD owner/policy")
        # Native CPU contract uses a full regular fftshift grid. Bind its
        # geometry/normalization; do not hash or allocate an axis per publication.
        if (len(frame.frequencies_hz) != actual.fft_size or len(frame.values) != actual.fft_size
                or not isfinite(float(frame.frequencies_hz[0]))
                or float(frame.frequencies_hz[0]) != actual.center_hz - actual.sample_rate_hz / 2
                or float(frame.frequencies_hz[-1]) != actual.center_hz + actual.sample_rate_hz / 2
                    - actual.sample_rate_hz / actual.fft_size):
            raise ValueError("processed native frame has incompatible regular FFT geometry")
        grid = (actual.center_hz, actual.sample_rate_hz, actual.fft_size, unit, "power-norm-v1")
        digest = self._grids.get(grid)
        if digest is None:
            digest = "sha256:" + sha256(b"regular-fftshift-grid-v1\0" +
                pack("!ddQ", actual.center_hz, actual.sample_rate_hz, actual.fft_size) +
                b"dBFS/bin\0power-norm-v1").hexdigest()
            self._grids[grid] = digest
            if len(self._grids) > 4:
                self._grids.popitem(last=False)
        key = ProcessingFrameKey(topology.physical_stream_resource_id, source, chain,
            current.active_config_generation, current.acquisition_epoch, digest, unit,
            "power-norm-v1", "cpu-pocketfft", float(actual.center_hz), float(actual.sample_rate_hz),
            float(actual.analog_bandwidth_hz))
        return AppliedProcessingContextV1(provenance.processing_recipe.policy_digest, revision,
            key, policy.dc_mode, policy.spur_mode, None,
            provenance.processing_recipe.whole_frame_modified, (), frame.quality_flags)
