"""Immutable presentation snapshots shared by UI commands and presenters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from .notifications import NotificationItem


class WorkspaceId(StrEnum):
    START = "start"
    LIVE_MONITOR = "live_monitor"
    WIDEBAND_SWEEP = "wideband_sweep"
    OFFLINE_DFL = "offline_dfl"
    CALIBRATION = "calibration"
    RECORDING_REPLAY = "recording_replay"
    DIAGNOSTICS = "diagnostics"


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class AcquisitionStatus(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class BackendStatus(StrEnum):
    UNKNOWN = "unknown"
    CPU = "cpu"
    CUDA = "cuda"
    HIP = "hip"
    UNAVAILABLE = "unavailable"


class CalibrationStatus(StrEnum):
    UNKNOWN = "unknown"
    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    WARNING = "warning"
    ERROR = "error"


class RecordingStatus(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


class HealthStatus(StrEnum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    DEGRADED = "degraded"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class AppliedValueStatus(StrEnum):
    MATCH = "match"
    ADJUSTED_BY_DEVICE = "adjusted_by_device"
    PENDING = "pending"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AppliedValue:
    requested: float | str | None
    applied: float | str | None
    unit: str
    status: AppliedValueStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionUiState:
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    endpoint_label: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionUiState:
    status: AcquisitionStatus = AcquisitionStatus.IDLE
    sample_rate: AppliedValue | None = None
    fft_rate_hz: float | None = None


@dataclass(frozen=True, slots=True)
class BackendUiState:
    status: BackendStatus = BackendStatus.UNKNOWN
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CalibrationUiState:
    status: CalibrationStatus = CalibrationStatus.UNKNOWN
    profile_name: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RecordingUiState:
    status: RecordingStatus = RecordingStatus.IDLE
    output_label: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HealthItem:
    key: str
    status: HealthStatus
    text: str
    icon_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HealthUiState:
    overall: HealthStatus = HealthStatus.DISCONNECTED
    items: tuple[HealthItem, ...] = ()


@dataclass(frozen=True, slots=True)
class AppUiState:
    """Single immutable snapshot; widgets must not mirror native truth."""

    active_workspace: WorkspaceId = WorkspaceId.START
    active_session_id: str | None = None
    connection: ConnectionUiState = field(default_factory=ConnectionUiState)
    acquisition: AcquisitionUiState = field(default_factory=AcquisitionUiState)
    backend: BackendUiState = field(default_factory=BackendUiState)
    calibration: CalibrationUiState = field(default_factory=CalibrationUiState)
    recording: RecordingUiState = field(default_factory=RecordingUiState)
    health: HealthUiState = field(default_factory=HealthUiState)
    notifications: tuple[NotificationItem, ...] = ()


@dataclass(frozen=True, slots=True)
class UiUpdateBatch:
    """A bounded UI update descriptor that never carries raw I/Q samples."""

    sequence: int
    state: AppUiState | None = None
    spectrum_revision: int | None = None
    waterfall_rows: int = 0
    persistence_revision: int | None = None
    health: HealthUiState | None = None
    measurement_updates: Mapping[str, str | float | int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.waterfall_rows < 0:
            raise ValueError("waterfall_rows must be non-negative")
