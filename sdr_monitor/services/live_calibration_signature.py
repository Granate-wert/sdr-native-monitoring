"""Exact readback-to-profile join; no SDK calls, defaults or profile activation."""

from dataclasses import dataclass

from ..domain.calibration import CalibrationProfileError, CalibrationSignature
from ..domain.device_capabilities import DeviceFamily
from ..domain.live import AppliedLiveConfiguration, BackendKind, DeviceDescriptor, ObservedGainMode
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
    if actual.backend is BackendKind.AUTO:
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
