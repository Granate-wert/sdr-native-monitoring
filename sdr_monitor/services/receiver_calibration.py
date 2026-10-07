"""Bounded single-RX selection scopes sharing immutable profile storage."""
from dataclasses import dataclass
from threading import RLock

from ..domain.calibration import CalibrationApplicability, CalibrationProfile, CalibrationProfileError, CalibrationSignature
from ..domain.device_capabilities import DeviceCalibrationIdentity, DeviceFamily
from ..domain.live import DeviceDescriptor
from ..domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint
from .calibration_service import CalibrationSelectionReceipt, CalibrationService
from .calibration_store import CalibrationProfileStore


@dataclass(frozen=True, slots=True)
class _ReceiverScope:
    identity: DeviceCalibrationIdentity
    source_id: str
    resource_id: str
    selection: ReceiverChainSelection


def _scope(device: DeviceDescriptor, endpoint: ReceiverEndpoint) -> _ReceiverScope:
    if not isinstance(device, DeviceDescriptor) or not isinstance(endpoint, ReceiverEndpoint):
        raise CalibrationProfileError('typed device/receiver required')
    identity, snapshot, topology = device.calibration_identity, device.capability_snapshot, device.capabilities.receiver_topology
    if (identity is None or snapshot is None or topology is None
            or identity.family in (DeviceFamily.UNKNOWN, DeviceFamily.TINYSA, DeviceFamily.GENERIC_INSTRUMENT)
            or snapshot.device_id != device.device_id or endpoint.source_id != device.device_id
            or identity.device_identity_key != snapshot.identity_key or identity.family is not snapshot.family
            or identity.adapter_id != snapshot.adapter_id
            or endpoint.selection is ReceiverChainSelection.BOTH
            or endpoint.physical_stream_resource_id != topology.physical_stream_resource_id
            or not topology.supports_selection(endpoint.selection)):
        raise CalibrationProfileError('confirmed single SDR receiver identity/topology required')
    return _ReceiverScope(identity, device.device_id, endpoint.physical_stream_resource_id, endpoint.selection)


class ReceiverCalibrationRegistry:
    """No hardware IO, auto-selection, persistence of activation or silent eviction.

    Logical pane/endpoint labels do not define RX identity. Two bindings to the
    same typed RX share selection; RX1/RX2 and changed identity/firmware/resource
    get different services. The descriptor must come from current admission.
    """

    def __init__(self, store: CalibrationProfileStore, *, max_scopes: int = 64) -> None:
        if not isinstance(store, CalibrationProfileStore):
            raise CalibrationProfileError('shared typed calibration store required')
        if type(max_scopes) is not int or not 1 <= max_scopes <= 128:
            raise CalibrationProfileError('receiver scope capacity must be an integer in 1..128')
        self.store = store
        self._max_scopes = max_scopes
        self._lock = RLock()
        self._services: dict[_ReceiverScope, CalibrationService] = {}

    def for_device(self, device: DeviceDescriptor, endpoint: ReceiverEndpoint) -> CalibrationService:
        key = _scope(device, endpoint)
        with self._lock:
            service = self._services.get(key)
            if service is None:
                if len(self._services) >= self._max_scopes:
                    raise CalibrationProfileError('receiver calibration capacity reached; release an explicit scope')
                service = CalibrationService(self.store)
                self._services[key] = service
            return service

    def is_current_service(
        self, device: DeviceDescriptor, endpoint: ReceiverEndpoint, service: CalibrationService,
    ) -> bool:
        key = _scope(device, endpoint)
        with self._lock:
            return self._services.get(key) is service

    def apply_selection(self, device: DeviceDescriptor, endpoint: ReceiverEndpoint,
                        service: CalibrationService, profile: CalibrationProfile | None,
                        settings: CalibrationSignature, expected: CalibrationSelectionReceipt
                        ) -> CalibrationApplicability | None:
        """Scope membership and selection CAS under one registry transaction."""
        key = _scope(device, endpoint)
        with self._lock:
            if self._services.get(key) is not service:
                raise CalibrationProfileError('receiver calibration scope was retired')
            return service.apply_checked_selection(profile, settings, expected)

    def release(self, device: DeviceDescriptor, endpoint: ReceiverEndpoint) -> None:
        key = _scope(device, endpoint)
        with self._lock:
            service = self._services.pop(key, None)
            if service is not None:
                service.clear_active_profile()  # Invalidate retained selection receipts too.

    def clear(self) -> None:
        with self._lock:
            for service in self._services.values():
                service.clear_active_profile()
            self._services.clear()
