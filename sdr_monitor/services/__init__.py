"""Qt-free service contracts and the standalone SDR composition root."""

from __future__ import annotations

from .calibration_service import CalibrationService
from .calibration_store import CalibrationProfileStore
from .recording_session import InMemoryRecordingService, RecordingService, RecordingSourceBus
from .replay_session import RecordingReader, ReplayService
from .diagnostics_session import DiagnosticsService, TaskSupervisor
from .sdr_application_services import SdrApplicationServices, build_default_sdr_services
from .native_live import NativeLiveSessionService
from .sweep_session import InMemorySweepService
from .hackrf_capability_adapter import (
    HACKRF_LIBHACKRF_ADAPTER_ID,
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfCapabilityObservation,
    HackrfCapabilityObservationError,
    HackrfReadOnlyProbe,
    HackrfReadOnlyProbePort,
)
from .hackrf_live_admission import (
    HackrfLiveActivationPlan,
    HackrfLiveAdmission,
    HackrfLiveAdmissionReason,
    HackrfLiveRequest,
    admit_hackrf_live,
)
from .hackrf_native_factory import (
    HackrfNativeFactoryError,
    HackrfNativeFactoryFailure,
    HackrfNativeRuntimeFactory,
)
from .hackrf_activation_preflight import (
    HackrfActivationPreflight,
    HackrfActivationPreflightReason,
    HackrfActivationPreflightService,
    HackrfActivationPermit,
    HackrfRuntimeIdentityPort,
    HackrfRuntimeIdentityProbe,
)
from .libhackrf_runtime_identity import LibhackrfRuntimeIdentityPort

__all__ = [
    "InMemoryRecordingService",
    "RecordingService",
    "RecordingSourceBus",
    "RecordingReader",
    "ReplayService",
    "DiagnosticsService",
    "TaskSupervisor",
    "CalibrationProfileStore",
    "CalibrationService",
    "InMemorySweepService",
    "NativeLiveSessionService",
    "SdrApplicationServices",
    "build_default_sdr_services",
    "HACKRF_LIBHACKRF_ADAPTER_ID",
    "HackrfBoardKind",
    "HackrfCapabilityAdapter",
    "HackrfCapabilityObservation",
    "HackrfCapabilityObservationError",
    "HackrfReadOnlyProbe",
    "HackrfReadOnlyProbePort",
    "HackrfLiveActivationPlan",
    "HackrfLiveAdmission",
    "HackrfLiveAdmissionReason",
    "HackrfLiveRequest",
    "admit_hackrf_live",
    "HackrfNativeFactoryError",
    "HackrfNativeFactoryFailure",
    "HackrfNativeRuntimeFactory",
    "HackrfActivationPreflight",
    "HackrfActivationPreflightReason",
    "HackrfActivationPreflightService",
    "HackrfActivationPermit",
    "HackrfRuntimeIdentityPort",
    "HackrfRuntimeIdentityProbe",
    "LibhackrfRuntimeIdentityPort",
]
