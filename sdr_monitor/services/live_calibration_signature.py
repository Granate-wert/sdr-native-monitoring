"""Exact readback-to-profile join; no SDK calls, defaults or profile activation."""

from dataclasses import dataclass

from ..domain.calibration import CalibrationProfileError, CalibrationSignature
from ..domain.device_capabilities import DeviceFamily
from ..domain.live import (
    AppliedLiveConfiguration, BackendKind, DeviceDescriptor, LiveSessionState,
    LiveSnapshot, LiveSpectrumFrame, ObservedGainMode,
)
from ..domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint
from ..domain.spectrum_provenance import SpectrumProvenance


@dataclass(frozen=True, slots=True)
class CalibrationFrontendContext:
    """Explicit operator/reference setup, not inferred from a digital RX number."""

    rf_port_path: str
    frontend_chain: str
    reference_plane: str

    def __post_init__(self) -> None:
        for name in ("rf_port_path", "frontend_chain", "reference_plane"):
            value = getattr(self, name)
            if (not isinstance(value, str) or not value.strip() or value.strip() == "unknown"
                    or len(value) > 128):
                raise CalibrationProfileError(f"explicit {name} is required")


def build_live_calibration_signature(
    device: DeviceDescriptor,
    endpoint: ReceiverEndpoint,
    applied: AppliedLiveConfiguration,
    provenance: SpectrumProvenance,
    frontend: CalibrationFrontendContext,
    *,
    unit: str,
) -> CalibrationSignature:
    """Join admitted facts for one RX; incomplete current producers refuse.

    The caller must supply the receipt and provenance for the same admitted
    acquisition. This pure join does not prove their epoch association, RF
    path accuracy, calibration correction, or a physical reference signal.
    AGC profiles refuse: one readback gain is not a stable AGC transfer curve.
    """
    if not isinstance(device, DeviceDescriptor) or not isinstance(endpoint, ReceiverEndpoint):
        raise CalibrationProfileError("typed device and receiver endpoint required")
    if not isinstance(applied, AppliedLiveConfiguration) or not isinstance(provenance, SpectrumProvenance):
        raise CalibrationProfileError("typed applied readback and numerical provenance required")
    if not isinstance(frontend, CalibrationFrontendContext):
        raise CalibrationProfileError("explicit typed frontend context required")
    identity = device.calibration_identity
    snapshot = device.capability_snapshot
    if identity is None or snapshot is None or identity.family in (
        DeviceFamily.UNKNOWN, DeviceFamily.TINYSA, DeviceFamily.GENERIC_INSTRUMENT,
    ):
        raise CalibrationProfileError("admitted SDR calibration identity required")
    if (snapshot.device_id != device.device_id or endpoint.source_id != device.device_id
            or endpoint.selection is ReceiverChainSelection.BOTH):
        raise CalibrationProfileError("calibration requires one RX of this exact source")
    topology = device.capabilities.receiver_topology
    if (topology is None or topology.physical_stream_resource_id != endpoint.physical_stream_resource_id
            or not topology.supports_selection(endpoint.selection)):
        raise CalibrationProfileError("receiver topology/resource is not confirmed")
    required = {"center_hz", "sample_rate_hz", "analog_bandwidth_hz", "gain_db", "gain_mode"}
    if not required.issubset(applied.readback_fields):
        raise CalibrationProfileError("incomplete hardware readback")
    if applied.observed_gain_mode is not ObservedGainMode.MANUAL:
        raise CalibrationProfileError("stable manual gain readback required; AGC calibration unsupported")
    actual = applied.applied
    if not isinstance(actual.backend, BackendKind) or actual.backend is BackendKind.AUTO:
        raise CalibrationProfileError("actual DSP backend required")
    if (provenance.window_normalization_version is None or provenance.window is None
            or provenance.window_normalization_version.strip() == "unknown"):
        raise CalibrationProfileError("producer normalization version and window required")
    if unit not in {"dBFS/bin", "dBFS/Hz"}:
        raise CalibrationProfileError("raw digital power unit required")
    # Identity is opaque and authoritative; serial/URI/name never replace it.
    return CalibrationSignature(
        device_serial=device.serial or "unknown", backend=actual.backend.value,
        rf_port_path=frontend.rf_port_path, frontend_chain=frontend.frontend_chain,
        reference_plane=frontend.reference_plane, sample_rate_hz=actual.sample_rate_hz,
        analog_bandwidth_hz=actual.analog_bandwidth_hz, gain_mode=applied.observed_gain_mode.value,
        manual_gain_db=actual.gain_db, window_normalization_version=provenance.window_normalization_version,
        fft_unit_convention=unit, receiver_chain=endpoint.selection.chains[0],
        device_family=identity.family.value, adapter_id=identity.adapter_id,
        device_identity_key=identity.device_identity_key, firmware_fingerprint=identity.firmware_fingerprint,
    )


def build_current_frame_calibration_signature(
    current: LiveSnapshot,
    frame: LiveSpectrumFrame,
    endpoint: ReceiverEndpoint,
    frontend: CalibrationFrontendContext,
) -> CalibrationSignature:
    """Admit the original current frame, not a stale/equal clone.

    Caller supplies the current immutable owner snapshot; this function does
    not fetch hardware/session state or authorize use of a retained old snapshot.
    Endpoint identifies the logical device/RX; active_source_id separately
    identifies the actual producer (distinct for paired streams).
    """
    if not isinstance(current, LiveSnapshot) or not isinstance(frame, LiveSpectrumFrame):
        raise CalibrationProfileError("typed current snapshot and spectrum required")
    if not isinstance(endpoint, ReceiverEndpoint):
        raise CalibrationProfileError("typed receiver endpoint required")
    if (current.state is not LiveSessionState.RUNNING or current.spectrum is not frame
            or current.device is None or current.applied is None or str(current.session_id) == "unknown"):
        raise CalibrationProfileError("original running owner publication required")
    if (endpoint.selection is ReceiverChainSelection.BOTH or current.receiver_id is None
            or current.receiver_id != endpoint.selection.value.upper() or frame.receiver_id != current.receiver_id):
        raise CalibrationProfileError("current typed RX admission differs from frame")
    if (current.active_source_id is None or str(current.active_source_id) == "unknown"
            or frame.source_id != current.active_source_id
            or type(current.active_config_generation) is not int
            or frame.config_generation != current.active_config_generation
            or type(current.acquisition_epoch) is not int or current.acquisition_epoch < 0
            or frame.acquisition_epoch != current.acquisition_epoch
            or current.clock_domain != "host_steady_ns" or frame.clock_domain != current.clock_domain
            or frame.unit != current.unit or frame.numerical_provenance is None):
        raise CalibrationProfileError("frame source/generation/epoch/clock/unit is not current")
    actual = current.applied.applied
    if (frame.center_frequency_hz != actual.center_hz or frame.sample_rate_hz != actual.sample_rate_hz
            or frame.fft_size != actual.fft_size
            or frame.hop_size != max(1, int(round(actual.fft_size * (1 - actual.overlap_ratio))))):
        raise CalibrationProfileError("frame geometry differs from applied acquisition")
    if frame.backend_discontinuity or current.quality.backend != actual.backend:
        raise CalibrationProfileError("backend transition requires a fresh applied receipt")
    provenance = frame.numerical_provenance
    if (provenance.window != actual.window or provenance.detector != actual.detector
            or provenance.averaging_frames != actual.averaging_frames):
        raise CalibrationProfileError("exact canonical applied DSP semantics required")
    return build_live_calibration_signature(
        current.device, endpoint, current.applied, provenance, frontend, unit=frame.unit,
    )
