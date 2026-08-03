"""Immutable domain state shared by Home, Live and future device adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DeviceTransport(StrEnum):
    USB = "usb"
    IP = "ip"
    MANUAL = "manual"


class BackendKind(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"
    HIP = "hip"


class LiveSessionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class CalibrationQuality(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    sample_rates_hz: tuple[float, ...]
    gain_range_db: tuple[float, float]
    supported_backends: tuple[BackendKind, ...] = (BackendKind.AUTO, BackendKind.CPU)


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    device_id: str
    label: str
    uri: str
    transport: DeviceTransport
    capabilities: DeviceCapabilities


@dataclass(frozen=True, slots=True)
class LiveConfiguration:
    center_hz: float = 2.4e9
    sample_rate_hz: float = 20e6
    gain_db: float = 18.0
    backend: BackendKind = BackendKind.AUTO
    profile_id: str | None = None

    def __post_init__(self) -> None:
        if self.center_hz <= 0 or self.sample_rate_hz <= 0:
            raise ValueError("center and sample rate must be positive")


@dataclass(frozen=True, slots=True)
class AppliedLiveConfiguration:
    requested: LiveConfiguration
    applied: LiveConfiguration
    adjustments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveQuality:
    calibration: CalibrationQuality = CalibrationQuality.UNCALIBRATED
    backend: BackendKind = BackendKind.CPU
    fallback_reason: str | None = None
    dropped_blocks: int = 0


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    generation: int
    sequence: int
    state: LiveSessionState
    device: DeviceDescriptor | None = None
    applied: AppliedLiveConfiguration | None = None
    quality: LiveQuality = field(default_factory=LiveQuality)
    unit: str = "dBFS/bin"
    error: str | None = None

    @property
    def reports_dbm(self) -> bool:
        return self.unit.casefold().startswith("dbm") and self.quality.calibration is CalibrationQuality.CALIBRATED
