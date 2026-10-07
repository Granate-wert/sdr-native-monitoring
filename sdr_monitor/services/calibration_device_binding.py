"""Validate admitted operational/canonical identity; no discovery or ID parsing."""
from ..domain.calibration import CalibrationProfileError
from ..domain.device_capabilities import DeviceCapabilityBinding, DeviceFamily
from ..domain.live import DeviceDescriptor


def confirmed_calibration_device_binding(device: DeviceDescriptor) -> DeviceCapabilityBinding:
    if not isinstance(device, DeviceDescriptor):
        raise CalibrationProfileError("typed admitted device required")
    snapshot, identity = device.capability_snapshot, device.calibration_identity
    if (snapshot is None or identity is None or identity.family in (
            DeviceFamily.UNKNOWN, DeviceFamily.TINYSA, DeviceFamily.GENERIC_INSTRUMENT)):
        raise CalibrationProfileError("admitted SDR calibration identity required")
    binding = device.capability_binding
    if binding is None:
        # Compatibility for descriptors already using canonical IDs. A distinct
        # operational ID MUST carry the receipt from its actual owning adapter.
        if snapshot.device_id != device.device_id:
            raise CalibrationProfileError("distinct operational/canonical IDs require admitted binding")
        try:
            return DeviceCapabilityBinding(device.device_id, identity.family, identity.adapter_id,
                                           snapshot, identity)
        except (TypeError, ValueError) as error:
            raise CalibrationProfileError("canonical calibration device binding is inconsistent") from error
    if (not isinstance(binding, DeviceCapabilityBinding) or binding.source_id != device.device_id
            or binding.snapshot != snapshot or binding.calibration_identity != identity):
        raise CalibrationProfileError("exact admitted calibration device binding required")
    return binding
