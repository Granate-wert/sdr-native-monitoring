"""Array-free current single-RX binding; cached admitted facts, never hardware IO."""
from dataclasses import dataclass

from ..domain.calibration import CalibrationProfileError, CalibrationSignature
from ..domain.live import AppliedLiveConfiguration, LiveSnapshot
from ..domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint
from .live_calibration_signature import CalibrationFrontendContext, build_current_frame_calibration_signature


@dataclass(frozen=True, slots=True)
class CurrentCalibrationBinding:
    endpoint: ReceiverEndpoint
    frontend: CalibrationFrontendContext
    signature: CalibrationSignature
    applied: AppliedLiveConfiguration
    session_id: str
    acquisition_epoch: int
    config_generation: int
    control_revision: int
    authority: object


def current_calibration_binding(
    current: LiveSnapshot, frontend: CalibrationFrontendContext, revision: int, authority: object,
) -> CurrentCalibrationBinding:
    """Canonical RX metadata only; labels, serials and URI suffixes never select RX."""
    if (not isinstance(current, LiveSnapshot) or type(revision) is not int or revision < 0
            or current.device is None or current.spectrum is None or current.applied is None):
        raise CalibrationProfileError("current admitted SDR frame required for binding")
    topology = current.device.capabilities.receiver_topology
    if topology is None or current.receiver_id not in ("RX1", "RX2") or current.active_source_id is None:
        raise CalibrationProfileError("canonical current single-RX topology and producer required")
    selection = ReceiverChainSelection.RX1 if current.receiver_id == "RX1" else ReceiverChainSelection.RX2
    endpoint = ReceiverEndpoint(str(current.active_source_id), current.device.device_id,
                                topology.physical_stream_resource_id, selection)
    signature = build_current_frame_calibration_signature(current, current.spectrum, endpoint, frontend)
    # Signature builder has already checked these exact int values and readbacks.
    assert current.acquisition_epoch is not None and current.active_config_generation is not None
    return CurrentCalibrationBinding(endpoint, frontend, signature, current.applied,
                                     str(current.session_id), current.acquisition_epoch,
                                     int(current.active_config_generation), revision, authority)
