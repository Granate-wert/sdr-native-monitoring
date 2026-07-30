"""Canonical versioned SDR contracts shared by Python and the native core."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, is_dataclass
from enum import IntFlag, StrEnum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

import numpy as np

from ..domain import SourceDescriptor


CONTRACT_SCHEMA_NAME = "sdr-native-contracts"
# Version 2: P04 adds EngineState, OverflowPolicy and EventSeverity wire enums.
# Version 3: P05 restricts DspConfig.fft_size to power-of-two in [256, 262144].
# Version 4: P08 adds ComputeBackendKind and BackendErrorCode (P08H-00).
CONTRACT_SCHEMA_VERSION = 5
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1


class ContractValidationError(ValueError):
    """Raised before a malformed contract can cross the native boundary."""


class SourceType(StrEnum):
    DFL_FILE = "dfl_file"
    LIVE_IQ = "live_iq"
    RECORDED_IQ = "recorded_iq"
    LIVE_SCALAR_SWEEP = "live_scalar_sweep"
    RECORDED_SPECTRUM = "recorded_spectrum"
    SYNTHETIC = "synthetic"


class SpectrumUnit(StrEnum):
    DBFS_BIN = "dBFS/bin"
    DBFS_HZ = "dBFS/Hz"
    DBM_BIN = "dBm/bin"
    DBM_HZ = "dBm/Hz"
    DBM = "dBm"


class SampleFormat(StrEnum):
    COMPLEX_INT8_INTERLEAVED = "ci8_interleaved"
    COMPLEX_INT12_IN_INT16_LE = "ci12_in_i16_le"
    COMPLEX_INT16_LE = "ci16_le"
    COMPLEX_FLOAT32_LE = "cf32_le"


class WindowType(StrEnum):
    RECTANGULAR = "rectangular"
    HANN = "hann"
    BLACKMAN_HARRIS_4TERM = "blackman_harris_4term"
    FLAT_TOP = "flat_top"
    NUTTALL = "nuttall"
    KAISER = "kaiser"


class DetectorType(StrEnum):
    SAMPLE = "sample"
    PEAK = "peak"
    NEGATIVE_PEAK = "negative_peak"
    RMS = "rms"
    AVERAGE_POWER = "average_power"


class GainMode(StrEnum):
    MANUAL = "manual"
    SLOW_ATTACK = "slow_attack"
    FAST_ATTACK = "fast_attack"
    HYBRID = "hybrid"


class DeviceState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    CONFIGURING = "configuring"
    STREAMING = "streaming"
    SWEEPING = "sweeping"
    RECORDING = "recording"
    DEGRADED = "degraded"
    ERROR = "error"
    STOPPING = "stopping"
    SHUTTING_DOWN = "shutting_down"


class CalibrationStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNCALIBRATED = "uncalibrated"
    APPLIED = "applied"
    INTERPOLATED = "interpolated"
    EXTRAPOLATED = "extrapolated"
    INVALID = "invalid"


class QualityFlag(IntFlag):
    NONE = 0
    UNCALIBRATED = 1 << 0
    CALIBRATION_INTERPOLATED = 1 << 1
    CALIBRATION_EXTRAPOLATED = 1 << 2
    GAIN_MODE_AGC = 1 << 3
    ADC_OVERLOAD = 1 << 4
    IQ_DROPPED = 1 << 5
    FFT_DROPPED = 1 << 6
    SETTLING_INCOMPLETE = 1 << 7
    EDGE_BIN = 1 << 8
    DC_REMOVED = 1 << 9
    LO_LEAKAGE_REGION = 1 << 10
    STITCH_OVERLAP = 1 << 11
    MISSING_SEGMENT = 1 << 12
    TIMESTAMP_ESTIMATED = 1 << 13
    BACKEND_FALLBACK = 1 << 14


class BackendKind(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"


class ComputeBackendKind(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"
    HIP = "hip"


class BackendErrorCode(StrEnum):
    NONE = "none"
    RUNTIME_NOT_FOUND = "runtime_not_found"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    NO_DEVICE = "no_device"
    UNSUPPORTED_DEVICE = "unsupported_device"
    ALLOCATION_FAILED = "allocation_failed"
    COPY_FAILED = "copy_failed"
    KERNEL_LAUNCH_FAILED = "kernel_launch_failed"
    FFT_PLAN_FAILED = "fft_plan_failed"
    FFT_EXECUTION_FAILED = "fft_execution_failed"
    DEVICE_LOST = "device_lost"
    TIMEOUT_OR_TDR = "timeout_or_tdr"
    NUMERICAL_SELF_TEST_FAILED = "numerical_self_test_failed"
    UNKNOWN = "unknown"


class PrecisionMode(StrEnum):
    REFERENCE_F64 = "reference_f64"
    ACCURATE_F32_F64_ACCUM = "accurate_f32_f64_accum"
    FAST_F32 = "fast_f32"


class PersistenceMode(StrEnum):
    DISABLED = "disabled"
    ROLLING_EXACT = "rolling_exact"
    EXPONENTIAL_DECAY = "exponential_decay"


class EngineState(StrEnum):
    CREATED = "created"
    CONFIGURED = "configured"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class OverflowPolicy(StrEnum):
    BLOCK = "block"
    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"
    LATEST_WINS = "latest_wins"


class EventSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


STRING_ENUM_TYPES = (
    SourceType,
    SpectrumUnit,
    SampleFormat,
    WindowType,
    DetectorType,
    GainMode,
    DeviceState,
    CalibrationStatus,
    BackendKind,
    ComputeBackendKind,
    BackendErrorCode,
    PrecisionMode,
    PersistenceMode,
    EngineState,
    OverflowPolicy,
    EventSeverity,
)


def enum_wire_schema() -> dict[str, dict[str, str | int]]:
    schema: dict[str, dict[str, str | int]] = {
        enum_type.__name__: {str(item.name): item.value for item in enum_type}
        for enum_type in STRING_ENUM_TYPES
    }
    schema["QualityFlag"] = {"NONE": 0, **{str(item.name): item.value for item in QualityFlag}}
    return schema


def _enum(value: object, enum_type: type[StrEnum], name: str) -> None:
    if not isinstance(value, enum_type):
        raise ContractValidationError(f"{name} must be {enum_type.__name__}")


def _flags(value: object, name: str) -> None:
    if not isinstance(value, QualityFlag):
        raise ContractValidationError(f"{name} must be QualityFlag")
    known = sum(item.value for item in QualityFlag)
    if int(value) & ~known:
        raise ContractValidationError(f"{name} contains unknown flag bits")


def _finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ContractValidationError(f"{name} must be finite")
    return result


def _positive(value: float, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise ContractValidationError(f"{name} must be positive")
    return result


def _uint(value: int, name: str, maximum: int, *, zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(f"{name} must be an integer")
    minimum = 0 if zero else 1
    if not minimum <= value <= maximum:
        raise ContractValidationError(f"{name} must be in [{minimum}, {maximum}]")
    return value


def _schema(version: int) -> None:
    if version != CONTRACT_SCHEMA_VERSION:
        raise ContractValidationError(
            f"unsupported schema_version {version}; expected {CONTRACT_SCHEMA_VERSION}"
        )


def _json_value(value: object, path: str = "metadata") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError(f"{path} contains a non-finite float")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(f"{path} keys must be strings")
            _json_value(item, f"{path}.{key}")
        return
    raise ContractValidationError(f"{path} contains unsupported {type(value).__name__}")


def freeze_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    _json_value(metadata)
    return MappingProxyType(dict(metadata))


def calibrated_unit(unit: SpectrumUnit) -> bool:
    _enum(unit, SpectrumUnit, "unit")
    return unit in (SpectrumUnit.DBM, SpectrumUnit.DBM_BIN, SpectrumUnit.DBM_HZ)


def validate_unit_calibration(
    unit: SpectrumUnit,
    status: CalibrationStatus,
    profile_id: str | None,
) -> None:
    _enum(status, CalibrationStatus, "calibration_status")
    applied = status in (
        CalibrationStatus.APPLIED,
        CalibrationStatus.INTERPOLATED,
        CalibrationStatus.EXTRAPOLATED,
    )
    if calibrated_unit(unit) and (not applied or not profile_id):
        raise ContractValidationError("dBm units require an applicable calibration profile")
    if not calibrated_unit(unit) and applied:
        raise ContractValidationError("applied calibration is incompatible with dBFS units")


def _array(value: Any, dtype: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=dtype)
    if array.ndim != 1:
        raise ContractValidationError(f"{name} must be one-dimensional")
    capsule_owned = not array.flags.writeable and not isinstance(array.base, np.ndarray)
    if not capsule_owned or not array.flags.c_contiguous:
        array = np.array(array, dtype=dtype, order="C", copy=True)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class NumericRange:
    minimum: float
    maximum: float
    step: float | None = None

    def __post_init__(self) -> None:
        if _finite(self.maximum, "maximum") < _finite(self.minimum, "minimum"):
            raise ContractValidationError("maximum must not be less than minimum")
        if self.step is not None:
            _positive(self.step, "step")

    def contains(self, value: float) -> bool:
        candidate = _finite(value, "value")
        return self.minimum <= candidate <= self.maximum


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    source_id: str
    context_uri: str
    center_frequency_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    gain_mode: GainMode = GainMode.MANUAL
    manual_gain_db: float = 0.0
    channel_index: int = 0
    buffer_samples: int = 262_144
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.context_uri.strip():
            raise ContractValidationError("source_id and context_uri must not be empty")
        _positive(self.center_frequency_hz, "center_frequency_hz")
        _positive(self.sample_rate_hz, "sample_rate_hz")
        _positive(self.analog_bandwidth_hz, "analog_bandwidth_hz")
        _enum(self.gain_mode, GainMode, "gain_mode")
        _finite(self.manual_gain_db, "manual_gain_db")
        _uint(self.channel_index, "channel_index", UINT32_MAX)
        _uint(self.buffer_samples, "buffer_samples", UINT32_MAX, zero=False)
        _schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class DspConfig:
    fft_size: int
    hop_size: int
    window: WindowType = WindowType.HANN
    detector: DetectorType = DetectorType.SAMPLE
    unit: SpectrumUnit = SpectrumUnit.DBFS_BIN
    precision_mode: PrecisionMode = PrecisionMode.ACCURATE_F32_F64_ACCUM
    batch_size: int = 1
    averaging_frames: int = 1
    kaiser_beta: float = 8.6
    calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED
    calibration_profile_id: str | None = None
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _uint(self.fft_size, "fft_size", UINT32_MAX, zero=False)
        # P05 CPU DSP contract: power-of-two FFT in [256, 262144].
        if (
            self.fft_size < 256
            or self.fft_size > 262_144
            or self.fft_size & (self.fft_size - 1)
        ):
            raise ContractValidationError(
                "fft_size must be a power of two in [256, 262144]"
            )
        _uint(self.hop_size, "hop_size", UINT32_MAX, zero=False)
        if self.hop_size > self.fft_size:
            raise ContractValidationError("hop_size must not exceed fft_size")
        _enum(self.window, WindowType, "window")
        _enum(self.detector, DetectorType, "detector")
        _enum(self.precision_mode, PrecisionMode, "precision_mode")
        _uint(self.batch_size, "batch_size", UINT32_MAX, zero=False)
        _uint(self.averaging_frames, "averaging_frames", UINT32_MAX, zero=False)
        if _finite(self.kaiser_beta, "kaiser_beta") < 0.0:
            raise ContractValidationError("kaiser_beta must not be negative")
        validate_unit_calibration(
            self.unit,
            self.calibration_status,
            self.calibration_profile_id,
        )
        _schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class PersistenceConfig:
    enabled: bool = False
    mode: PersistenceMode = PersistenceMode.DISABLED
    window_frames: int = 500
    half_life_seconds: float = 1.0
    power_min_db: float = -140.0
    power_max_db: float = 20.0
    power_bins: int = 256
    snapshot_rate_hz: float = 30.0
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ContractValidationError("enabled must be bool")
        _enum(self.mode, PersistenceMode, "mode")
        if self.enabled != (self.mode is not PersistenceMode.DISABLED):
            raise ContractValidationError("enabled and mode disagree")
        _uint(self.window_frames, "window_frames", UINT32_MAX, zero=False)
        _positive(self.half_life_seconds, "half_life_seconds")
        if _finite(self.power_max_db, "power_max_db") <= _finite(
            self.power_min_db,
            "power_min_db",
        ):
            raise ContractValidationError("power_max_db must exceed power_min_db")
        _uint(self.power_bins, "power_bins", UINT32_MAX, zero=False)
        if self.power_bins < 2:
            raise ContractValidationError("power_bins must be at least 2")
        _positive(self.snapshot_rate_hz, "snapshot_rate_hz")
        _schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class SweepConfig:
    start_frequency_hz: float
    stop_frequency_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    overlap_hz: float
    fft_size: int
    hop_size: int
    dwell_frames: int = 1
    settling_time_seconds: float = 0.0
    discard_blocks: int = 0
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        start = _positive(self.start_frequency_hz, "start_frequency_hz")
        stop = _positive(self.stop_frequency_hz, "stop_frequency_hz")
        if stop <= start:
            raise ContractValidationError("stop_frequency_hz must exceed start_frequency_hz")
        sample_rate = _positive(self.sample_rate_hz, "sample_rate_hz")
        bandwidth = _positive(self.analog_bandwidth_hz, "analog_bandwidth_hz")
        overlap = _finite(self.overlap_hz, "overlap_hz")
        if not 0.0 <= overlap < min(sample_rate, bandwidth):
            raise ContractValidationError("overlap_hz is outside the usable width")
        _uint(self.fft_size, "fft_size", UINT32_MAX, zero=False)
        _uint(self.hop_size, "hop_size", UINT32_MAX, zero=False)
        if self.hop_size > self.fft_size:
            raise ContractValidationError("hop_size must not exceed fft_size")
        _uint(self.dwell_frames, "dwell_frames", UINT32_MAX, zero=False)
        if _finite(self.settling_time_seconds, "settling_time_seconds") < 0.0:
            raise ContractValidationError("settling_time_seconds must not be negative")
        _uint(self.discard_blocks, "discard_blocks", UINT32_MAX)
        _schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class RecordingConfig:
    enabled: bool = False
    output_uri: str | None = None
    record_iq: bool = False
    record_spectrum: bool = False
    chunk_samples: int = 1_048_576
    queue_capacity: int = 8
    stop_on_overflow: bool = True
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        switches = (
            self.enabled,
            self.record_iq,
            self.record_spectrum,
            self.stop_on_overflow,
        )
        if not all(isinstance(value, bool) for value in switches):
            raise ContractValidationError("recording switches must be bool")
        if self.enabled and (
            not self.output_uri
            or not self.output_uri.strip()
            or not (self.record_iq or self.record_spectrum)
        ):
            raise ContractValidationError("enabled recording needs output and a stream")
        _uint(self.chunk_samples, "chunk_samples", UINT32_MAX, zero=False)
        _uint(self.queue_capacity, "queue_capacity", UINT32_MAX, zero=False)
        _schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    backend_id: str
    device_id: str
    serial: str
    model: str
    firmware: str
    tuning_range_hz: NumericRange
    sample_rate_ranges_hz: tuple[NumericRange, ...]
    analog_bandwidth_ranges_hz: tuple[NumericRange, ...]
    gain_range_db: NumericRange
    gain_modes: tuple[GainMode, ...]
    sample_formats: tuple[SampleFormat, ...]
    supports_hardware_timestamps: bool = False
    supports_fastlock: bool = False
    supports_temperature: bool = False
    supports_overflow_counter: bool = False
    supports_continuous_iq: bool = True
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.backend_id.strip() or not self.device_id.strip() or not self.model.strip():
            raise ContractValidationError("backend_id, device_id and model are required")
        ranges = (
            self.tuning_range_hz,
            self.gain_range_db,
            *self.sample_rate_ranges_hz,
            *self.analog_bandwidth_ranges_hz,
        )
        if not self.sample_rate_ranges_hz or not self.analog_bandwidth_ranges_hz:
            raise ContractValidationError("capability rate ranges must not be empty")
        if not all(isinstance(item, NumericRange) for item in ranges):
            raise ContractValidationError("capability ranges must be NumericRange")
        if not self.gain_modes or not all(isinstance(item, GainMode) for item in self.gain_modes):
            raise ContractValidationError("gain_modes must contain GainMode")
        if not self.sample_formats or not all(
            isinstance(item, SampleFormat) for item in self.sample_formats
        ):
            raise ContractValidationError("sample_formats must contain SampleFormat")
        _schema(self.schema_version)


@dataclass(frozen=True, slots=True)
class IqBlock:
    source_sequence: int
    first_sample_index: int
    timestamp_ns: int
    center_frequency_hz: float
    sample_rate_hz: float
    sample_format: SampleFormat
    sample_count: int
    flags: QualityFlag
    samples: np.ndarray
    config_generation: int

    def __post_init__(self) -> None:
        _uint(self.source_sequence, "source_sequence", UINT64_MAX)
        _uint(self.first_sample_index, "first_sample_index", UINT64_MAX)
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int):
            raise ContractValidationError("timestamp_ns must be an integer")
        _positive(self.center_frequency_hz, "center_frequency_hz")
        _positive(self.sample_rate_hz, "sample_rate_hz")
        _enum(self.sample_format, SampleFormat, "sample_format")
        _uint(self.sample_count, "sample_count", UINT32_MAX, zero=False)
        _flags(self.flags, "flags")
        _uint(self.config_generation, "config_generation", UINT64_MAX)
        samples = _array(self.samples, np.uint8, "samples")
        width = {
            SampleFormat.COMPLEX_INT8_INTERLEAVED: 2,
            SampleFormat.COMPLEX_INT12_IN_INT16_LE: 4,
            SampleFormat.COMPLEX_INT16_LE: 4,
            SampleFormat.COMPLEX_FLOAT32_LE: 8,
        }[self.sample_format]
        if samples.size != self.sample_count * width:
            raise ContractValidationError("samples length disagrees with format/count")
        object.__setattr__(self, "samples", samples)


@dataclass(frozen=True, slots=True)
class SpectrumFrame:
    source: SourceDescriptor
    frame_sequence: int
    first_sample_index: int
    timestamp_ns: int
    config_generation: int
    center_frequency_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    fft_bin_width_hz: float
    enbw_hz: float
    nominal_rbw_hz: float
    fft_size: int
    hop_size: int
    window: WindowType
    detector: DetectorType
    precision_mode: PrecisionMode
    unit: SpectrumUnit
    frequencies_hz: np.ndarray
    values: np.ndarray
    calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED
    calibration_profile_id: str | None = None
    estimated_uncertainty_db: float = float("nan")
    dropped_samples_before: int = 0
    dropped_iq_blocks_before: int = 0
    dropped_fft_frames_before: int = 0
    quality_flags: QualityFlag = QualityFlag.UNCALIBRATED

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceDescriptor):
            raise ContractValidationError("source must be SourceDescriptor")
        _uint(self.frame_sequence, "frame_sequence", UINT64_MAX)
        _uint(self.first_sample_index, "first_sample_index", UINT64_MAX)
        _uint(self.config_generation, "config_generation", UINT64_MAX)
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int):
            raise ContractValidationError("timestamp_ns must be an integer")
        for name in (
            "center_frequency_hz",
            "sample_rate_hz",
            "analog_bandwidth_hz",
            "fft_bin_width_hz",
            "enbw_hz",
            "nominal_rbw_hz",
        ):
            _positive(float(getattr(self, name)), name)
        _uint(self.fft_size, "fft_size", UINT32_MAX, zero=False)
        _uint(self.hop_size, "hop_size", UINT32_MAX, zero=False)
        if self.hop_size > self.fft_size:
            raise ContractValidationError("hop_size must not exceed fft_size")
        _enum(self.window, WindowType, "window")
        _enum(self.detector, DetectorType, "detector")
        _enum(self.precision_mode, PrecisionMode, "precision_mode")
        validate_unit_calibration(
            self.unit,
            self.calibration_status,
            self.calibration_profile_id,
        )
        frequencies = _array(self.frequencies_hz, np.float64, "frequencies_hz")
        values = _array(self.values, np.float32, "values")
        if frequencies.size != self.fft_size or values.size != self.fft_size:
            raise ContractValidationError("spectrum arrays must match fft_size")
        if not np.all(np.isfinite(frequencies)):
            raise ContractValidationError("frequencies_hz must be finite")
        if np.any(np.isnan(values)):
            raise ContractValidationError("values must not contain NaN")
        uncertainty = float(self.estimated_uncertainty_db)
        if not math.isnan(uncertainty) and (
            not math.isfinite(uncertainty) or uncertainty < 0.0
        ):
            raise ContractValidationError("uncertainty must be NaN or non-negative")
        for name in (
            "dropped_samples_before",
            "dropped_iq_blocks_before",
            "dropped_fft_frames_before",
        ):
            _uint(int(getattr(self, name)), name, UINT64_MAX)
        _flags(self.quality_flags, "quality_flags")
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values", values)

    @classmethod
    def from_native(cls, frame: Any) -> "SpectrumFrame":
        return cls(
            source=source_descriptor_from_native(frame.source),
            frame_sequence=int(frame.frame_sequence),
            first_sample_index=int(frame.first_sample_index),
            timestamp_ns=int(frame.timestamp_ns),
            config_generation=int(frame.config_generation),
            center_frequency_hz=float(frame.center_frequency_hz),
            sample_rate_hz=float(frame.sample_rate_hz),
            analog_bandwidth_hz=float(frame.analog_bandwidth_hz),
            fft_bin_width_hz=float(frame.fft_bin_width_hz),
            enbw_hz=float(frame.enbw_hz),
            nominal_rbw_hz=float(frame.nominal_rbw_hz),
            fft_size=int(frame.fft_size),
            hop_size=int(frame.hop_size),
            window=WindowType[_native_name(frame.window)],
            detector=DetectorType[_native_name(frame.detector)],
            precision_mode=PrecisionMode[_native_name(frame.precision_mode)],
            unit=SpectrumUnit[_native_name(frame.unit)],
            frequencies_hz=frame.frequencies_hz,
            values=frame.values,
            calibration_status=CalibrationStatus[_native_name(frame.calibration_status)],
            calibration_profile_id=frame.calibration_profile_id or None,
            estimated_uncertainty_db=float(frame.estimated_uncertainty_db),
            dropped_samples_before=int(frame.dropped_samples_before),
            dropped_iq_blocks_before=int(frame.dropped_iq_blocks_before),
            dropped_fft_frames_before=int(frame.dropped_fft_frames_before),
            quality_flags=QualityFlag(int(frame.quality_flags)),
        )


@dataclass(frozen=True, slots=True)
class SweepSegmentMetadata:
    segment_index: int
    center_frequency_hz: float
    actual_start_hz: float
    actual_stop_hz: float
    quality_flags: QualityFlag = QualityFlag.NONE

    def __post_init__(self) -> None:
        _uint(self.segment_index, "segment_index", UINT32_MAX)
        center = _positive(self.center_frequency_hz, "center_frequency_hz")
        start = _positive(self.actual_start_hz, "actual_start_hz")
        stop = _positive(self.actual_stop_hz, "actual_stop_hz")
        if not start <= center <= stop:
            raise ContractValidationError("segment center must lie in its range")
        _flags(self.quality_flags, "quality_flags")


@dataclass(frozen=True, slots=True)
class SweepSpectrumFrame:
    sweep_id: int
    started_ns: int
    completed_ns: int
    requested_start_hz: float
    requested_stop_hz: float
    actual_start_hz: float
    actual_stop_hz: float
    nominal_rbw_hz: float
    frequencies_hz: np.ndarray
    values: np.ndarray
    quality_flags_per_bin: np.ndarray
    segments: tuple[SweepSegmentMetadata, ...] = ()

    def __post_init__(self) -> None:
        _uint(self.sweep_id, "sweep_id", UINT64_MAX)
        if self.completed_ns < self.started_ns:
            raise ContractValidationError("completed_ns precedes started_ns")
        pairs = (
            (self.requested_start_hz, self.requested_stop_hz),
            (self.actual_start_hz, self.actual_stop_hz),
        )
        if any(_positive(stop, "stop_hz") <= _positive(start, "start_hz") for start, stop in pairs):
            raise ContractValidationError("sweep stop must exceed start")
        _positive(self.nominal_rbw_hz, "nominal_rbw_hz")
        frequencies = _array(self.frequencies_hz, np.float64, "frequencies_hz")
        values = _array(self.values, np.float32, "values")
        flags = _array(self.quality_flags_per_bin, np.uint16, "quality_flags_per_bin")
        if not frequencies.size == values.size == flags.size:
            raise ContractValidationError("sweep arrays must have equal length")
        if not np.all(np.isfinite(frequencies)):
            raise ContractValidationError("sweep frequencies must be finite")
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "quality_flags_per_bin", flags)


@dataclass(frozen=True, slots=True)
class EngineMetrics:
    iq_samples_received: int = 0
    iq_samples_dropped: int = 0
    iq_blocks_received: int = 0
    iq_blocks_dropped: int = 0
    fft_frames_computed: int = 0
    fft_frames_dropped: int = 0
    analytical_fft_rate: float = 0.0
    spectrum_snapshots_emitted: int = 0
    waterfall_rows_emitted: int = 0
    persistence_updates: int = 0
    render_snapshots_applied: int = 0
    acquisition_queue_depth: int = 0
    dsp_queue_depth: int = 0
    recorder_queue_depth: int = 0
    cpu_processing_ms: float = 0.0
    gpu_processing_ms: float = 0.0
    h2d_ms: float = 0.0
    d2h_ms: float = 0.0
    end_to_end_latency_ms: float = 0.0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, int):
                _uint(value, item.name, UINT64_MAX)
            elif _finite(value, item.name) < 0.0:
                raise ContractValidationError(f"{item.name} must not be negative")


SerializableContract: TypeAlias = (
    SourceDescriptor
    | NumericRange
    | DeviceConfig
    | DspConfig
    | PersistenceConfig
    | SweepConfig
    | RecordingConfig
    | DeviceCapabilities
)


def _encode(value: object) -> object:
    if isinstance(value, (StrEnum, IntFlag)):
        return value.value
    if is_dataclass(value):
        return {item.name: _encode(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    return value


def contract_to_dict(value: SerializableContract) -> dict[str, object]:
    return {
        "schema": CONTRACT_SCHEMA_NAME,
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "kind": type(value).__name__,
        "data": _encode(value),
    }


def _serialized_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"{name} must be numeric")
    return float(value)


def _serialized_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(f"{name} must be an integer")
    return value


def _range(data: Mapping[str, object]) -> NumericRange:
    step = data.get("step")
    return NumericRange(
        _serialized_float(data["minimum"], "minimum"),
        _serialized_float(data["maximum"], "maximum"),
        None if step is None else _serialized_float(step, "step"),
    )


def contract_from_dict(payload: Mapping[str, object]) -> SerializableContract:
    if payload.get("schema") != CONTRACT_SCHEMA_NAME:
        raise ContractValidationError("unknown contract schema")
    _schema(_serialized_int(payload.get("schema_version", 0), "schema_version"))
    kind = payload.get("kind")
    raw = payload.get("data")
    if not isinstance(kind, str) or not isinstance(raw, Mapping):
        raise ContractValidationError("payload requires kind and mapping data")
    data = dict(raw)
    try:
        if kind == "SourceDescriptor":
            data["source_type"] = SourceType(data["source_type"])
            return SourceDescriptor(**data)
        if kind == "NumericRange":
            return _range(data)
        if kind == "DeviceConfig":
            data["gain_mode"] = GainMode(data["gain_mode"])
            return DeviceConfig(**data)
        if kind == "DspConfig":
            data["window"] = WindowType(data["window"])
            data["detector"] = DetectorType(data["detector"])
            data["unit"] = SpectrumUnit(data["unit"])
            data["precision_mode"] = PrecisionMode(data["precision_mode"])
            data["calibration_status"] = CalibrationStatus(data["calibration_status"])
            return DspConfig(**data)
        if kind == "PersistenceConfig":
            data["mode"] = PersistenceMode(data["mode"])
            return PersistenceConfig(**data)
        if kind == "SweepConfig":
            return SweepConfig(**data)
        if kind == "RecordingConfig":
            return RecordingConfig(**data)
        if kind == "DeviceCapabilities":
            data["tuning_range_hz"] = _range(data["tuning_range_hz"])
            data["sample_rate_ranges_hz"] = tuple(_range(item) for item in data["sample_rate_ranges_hz"])
            data["analog_bandwidth_ranges_hz"] = tuple(
                _range(item) for item in data["analog_bandwidth_ranges_hz"]
            )
            data["gain_range_db"] = _range(data["gain_range_db"])
            data["gain_modes"] = tuple(GainMode(item) for item in data["gain_modes"])
            data["sample_formats"] = tuple(SampleFormat(item) for item in data["sample_formats"])
            return DeviceCapabilities(**data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError(f"invalid {kind} payload: {exc}") from exc
    raise ContractValidationError(f"unknown contract kind: {kind}")


def contract_to_json(value: SerializableContract) -> str:
    return json.dumps(contract_to_dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contract_from_json(payload: str) -> SerializableContract:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContractValidationError(f"invalid contract JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ContractValidationError("contract JSON root must be an object")
    return contract_from_dict(decoded)


def _native_name(value: object) -> str:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value).rsplit(".", 1)[-1]


def source_descriptor_from_native(value: Any) -> SourceDescriptor:
    metadata = {str(key): json.loads(raw) for key, raw in dict(value.metadata_json).items()}
    return SourceDescriptor(
        SourceType[_native_name(value.source_type)],
        str(value.source_id),
        str(value.display_name),
        str(value.uri) or None,
        str(value.device_serial) or None,
        metadata,
        str(value.backend_id) or None,
        int(value.schema_version),
    )


def source_descriptor_to_native(value: SourceDescriptor) -> object:
    from .native_api import require_native

    native = require_native()
    metadata = {
        key: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for key, item in value.metadata.items()
    }
    return native.SourceDescriptor(
        getattr(native.SourceType, value.source_type.name),
        value.source_id,
        value.display_name,
        value.uri or "",
        value.device_serial or "",
        value.backend_id or "",
        value.schema_version,
        metadata,
    )


def config_to_native(
    value: DeviceConfig | DspConfig | PersistenceConfig | SweepConfig | RecordingConfig,
) -> object:
    from .native_api import require_native

    native = require_native()
    if isinstance(value, DeviceConfig):
        return native.DeviceConfig(
            value.source_id,
            value.context_uri,
            value.center_frequency_hz,
            value.sample_rate_hz,
            value.analog_bandwidth_hz,
            getattr(native.GainMode, value.gain_mode.name),
            value.manual_gain_db,
            value.channel_index,
            value.buffer_samples,
            value.schema_version,
        )
    if isinstance(value, DspConfig):
        return native.DspConfig(
            value.fft_size,
            value.hop_size,
            getattr(native.WindowType, value.window.name),
            getattr(native.DetectorType, value.detector.name),
            getattr(native.SpectrumUnit, value.unit.name),
            getattr(native.PrecisionMode, value.precision_mode.name),
            value.batch_size,
            value.averaging_frames,
            value.kaiser_beta,
            getattr(native.CalibrationStatus, value.calibration_status.name),
            value.calibration_profile_id or "",
            value.schema_version,
        )
    if isinstance(value, PersistenceConfig):
        return native.PersistenceConfig(
            value.enabled,
            getattr(native.PersistenceMode, value.mode.name),
            value.window_frames,
            value.half_life_seconds,
            value.power_min_db,
            value.power_max_db,
            value.power_bins,
            value.snapshot_rate_hz,
            value.schema_version,
        )
    if isinstance(value, SweepConfig):
        return native.SweepConfig(
            value.start_frequency_hz,
            value.stop_frequency_hz,
            value.sample_rate_hz,
            value.analog_bandwidth_hz,
            value.overlap_hz,
            value.fft_size,
            value.hop_size,
            value.dwell_frames,
            value.settling_time_seconds,
            value.discard_blocks,
            value.schema_version,
        )
    if isinstance(value, RecordingConfig):
        return native.RecordingConfig(
            value.enabled,
            value.output_uri or "",
            value.record_iq,
            value.record_spectrum,
            value.chunk_samples,
            value.queue_capacity,
            value.stop_on_overflow,
            value.schema_version,
        )
    raise TypeError(f"unsupported config type: {type(value).__name__}")


__all__ = [
    "BackendKind",
    "CONTRACT_SCHEMA_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "CalibrationStatus",
    "ComputeBackendKind",
    "BackendErrorCode",
    "ContractValidationError",
    "DetectorType",
    "DeviceCapabilities",
    "DeviceConfig",
    "DeviceState",
    "DspConfig",
    "EngineMetrics",
    "EngineState",
    "EventSeverity",
    "GainMode",
    "IqBlock",
    "NumericRange",
    "OverflowPolicy",
    "PersistenceConfig",
    "PersistenceMode",
    "PrecisionMode",
    "QualityFlag",
    "RecordingConfig",
    "SampleFormat",
    "SourceDescriptor",
    "SourceType",
    "SpectrumFrame",
    "SpectrumUnit",
    "SweepConfig",
    "SweepSegmentMetadata",
    "SweepSpectrumFrame",
    "WindowType",
    "calibrated_unit",
    "config_to_native",
    "contract_from_dict",
    "contract_from_json",
    "contract_to_dict",
    "contract_to_json",
    "enum_wire_schema",
    "freeze_metadata",
    "source_descriptor_from_native",
    "source_descriptor_to_native",
    "validate_unit_calibration",
]
