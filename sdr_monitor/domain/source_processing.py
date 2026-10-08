"""Versioned single-stream HOST receipt; no AD topology or RF-readback fiction.

The operational device ID is the selected catalog/session identity, NOT a
physical alias proof or calibration identity. Native receiver identity remains
unknown for these families. Pane endpoint binding is a separate graph contract.
"""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from .processing_policy import DC_REMOVED_MASK, HostDcMode, _digest, _text, _uint


class RfValueAuthority(StrEnum):
    READBACK = "sdk_readback"
    SDK_APPLIED = "sdk_applied_not_readback"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RfProcessingValue:
    value_hz: float | None
    authority: RfValueAuthority

    def __post_init__(self) -> None:
        if type(self.authority) is not RfValueAuthority:
            raise ValueError("typed RF value authority required")
        if self.authority is RfValueAuthority.UNKNOWN:
            if self.value_hz is not None:
                raise ValueError("unknown RF value cannot carry an invented number")
        elif (type(self.value_hz) is not float or not isfinite(self.value_hz)
                or self.value_hz <= 0):
            raise ValueError("known RF value must be finite positive binary64")


@dataclass(frozen=True, slots=True)
class SourceProcessingFrameKeyV2:
    family: str
    selected_device_id: str
    source_id: str
    session_id: str
    owner_run_id: str
    producer_instance_id: int
    config_generation: int
    acquisition_epoch: int
    grid_digest: str
    unit: str
    normalization_version: str
    backend_id: str
    center: RfProcessingValue
    sample_rate: RfProcessingValue
    analog_bandwidth: RfProcessingValue
    native_receiver_id: None = None

    def __post_init__(self) -> None:
        if type(self.family) is not str or self.family not in ("hackrf", "rtl_sdr"):
            raise ValueError("single-stream family required")
        for name in ("selected_device_id", "source_id", "session_id", "owner_run_id",
                     "normalization_version", "backend_id"):
            _text(getattr(self, name), name)
        for name in ("producer_instance_id", "config_generation", "acquisition_epoch"):
            _uint(getattr(self, name), name)
        _digest(self.grid_digest, "grid digest")
        if (self.unit != "dBFS/bin" or type(self.unit) is not str
                or self.normalization_version != "power-norm-v1"
                or self.backend_id != "cpu-pocketfft" or self.native_receiver_id is not None):
            raise ValueError("unsupported native processing geometry or receiver authority")
        if any(type(v) is not RfProcessingValue for v in (
                self.center, self.sample_rate, self.analog_bandwidth)):
            raise ValueError("typed RF quantities required")
        expected = (RfValueAuthority.SDK_APPLIED if self.family == "hackrf"
                    else RfValueAuthority.READBACK)
        if self.center.authority is not expected or self.sample_rate.authority is not expected:
            raise ValueError("RF center/Fs authority differs from family contract")
        if self.analog_bandwidth.authority is not (
                RfValueAuthority.SDK_APPLIED if self.family == "hackrf" else RfValueAuthority.UNKNOWN):
            raise ValueError("analog bandwidth authority differs from family contract")


@dataclass(frozen=True, slots=True)
class AppliedSourceProcessingContextV2:
    frame_key: SourceProcessingFrameKeyV2
    policy_digest: str
    processing_revision: int
    dc_mode: HostDcMode
    whole_frame_modified: bool
    native_quality_flags: int
    hardware_dc_tracking: None = None

    def __post_init__(self) -> None:
        if type(self.frame_key) is not SourceProcessingFrameKeyV2:
            raise ValueError("typed single-stream processing frame key required")
        _digest(self.policy_digest, "policy digest")
        _uint(self.processing_revision, "processing revision")
        _uint(self.native_quality_flags, "native quality flags", (1 << 32) - 1, 0)
        if (type(self.dc_mode) is not HostDcMode or type(self.whole_frame_modified) is not bool
                or self.whole_frame_modified != (self.dc_mode is HostDcMode.BLOCK_MEAN)
                or self.hardware_dc_tracking is not None
                or self.processing_revision != self.frame_key.config_generation):
            raise ValueError("unsupported single-stream processing receipt")
        if self.dc_mode is HostDcMode.BLOCK_MEAN and not self.native_quality_flags & DC_REMOVED_MASK:
            raise ValueError("BlockMean must preserve native DC_REMOVED evidence")
