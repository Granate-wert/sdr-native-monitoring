"""Qt-free service contracts and the standalone SDR composition root."""

from __future__ import annotations

from .calibration_service import CalibrationService
from .calibration_store import CalibrationProfileStore
from .recording_session import InMemoryRecordingService, RecordingService, RecordingSourceBus
from .replay_session import RecordingReader, ReplayService
from .diagnostics_session import DiagnosticsService, TaskSupervisor
from .sdr_application_services import (
    SdrApplicationServices,
    UnavailableNativeRecordingService,
    build_default_sdr_services,
    build_offscreen_smoke_sdr_services,
)
from .native_live import NativeLiveSessionService
from .native_continuous_sweep import (
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
    NativeContinuousSweepDisplayService,
)
from .native_continuous_sweep_factory import (
    ContinuousSweepPlanRequest,
    NativeContinuousSweepPlanFactory,
    NativeLiveContinuousSweepDisplayService,
)
from .native_recording import NativeLiveRecordingService
from .native_sweep import NativeLiveSweepService, NativeSweepLease, NativeSweepService, NativeSweepSource
from .sweep_session import InMemorySweepService
from .receiver_topology_inventory import inventory_receiver_topology
from .receiver_lease_manager import ReceiverLease, ReceiverLeaseManager
from .ad936x_capability_adapter import (
    AD936X_LIBIIO_ADAPTER_ID,
    Ad936xCapabilityObservation,
    Ad936xCapabilityObservationError,
    Ad936xLibiioCapabilityAdapter,
)
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
from .hackrf_product_live import (
    HackrfNativeFactoryPort,
    HackrfProductLiveCoordinator,
    HackrfProductLiveFailure,
    HackrfProductLiveSnapshot,
    HackrfProductLiveStartResult,
    HackrfProductLiveState,
    HackrfProductLiveStopResult,
    HackrfRuntimeControlPort,
)
from .tinysa_serial_settings_port import TinySaSerialSettingsCommandPort
from .tinysa_serial_source_backend import TinySaSerialSourceBackend
from .tinysa_source_composition import (
    TinySaComposedSource,
    TinySaIdentityAssurance,
    TinySaSourceCandidate,
    TinySaSourceCompositionError,
    TinySaSourceCompositionService,
    TinySaSourcePhase,
    TinySaSourceReason,
    TinySaSourceSnapshot,
    TinySaVerifiedSource,
)

__all__ = [
    "InMemoryRecordingService",
    "RecordingService",
    "RecordingSourceBus",
    "RecordingReader",
    "ReplayService",
    "DiagnosticsService",
    "TaskSupervisor",
    "CalibrationProfileStore",
    "CalibrationService", "ContinuousSweepDisplayMetrics", "ContinuousSweepDisplaySnapshot", "ContinuousSweepPlanRequest",
    "InMemorySweepService", "NativeContinuousSweepDisplayService", "NativeContinuousSweepPlanFactory", "NativeLiveContinuousSweepDisplayService", "NativeLiveSessionService",
    "NativeLiveRecordingService", "NativeLiveSweepService", "NativeSweepLease", "NativeSweepService",
    "NativeSweepSource", "ReceiverLease", "ReceiverLeaseManager", "SdrApplicationServices", "UnavailableNativeRecordingService", "build_default_sdr_services", "build_offscreen_smoke_sdr_services", "inventory_receiver_topology",
    "AD936X_LIBIIO_ADAPTER_ID", "Ad936xCapabilityObservation",
    "Ad936xCapabilityObservationError", "Ad936xLibiioCapabilityAdapter",
    "HACKRF_LIBHACKRF_ADAPTER_ID", "HackrfBoardKind", "HackrfCapabilityAdapter",
    "HackrfCapabilityObservation", "HackrfCapabilityObservationError",
    "HackrfReadOnlyProbe", "HackrfReadOnlyProbePort", "HackrfLiveActivationPlan",
    "HackrfLiveAdmission", "HackrfLiveAdmissionReason", "HackrfLiveRequest",
    "HackrfNativeFactoryError", "HackrfNativeFactoryFailure", "HackrfNativeRuntimeFactory",
    "HackrfActivationPreflight", "HackrfActivationPreflightReason",
    "HackrfActivationPreflightService", "HackrfActivationPermit",
    "HackrfRuntimeIdentityPort", "HackrfRuntimeIdentityProbe",
    "HackrfNativeFactoryPort", "HackrfProductLiveCoordinator",
    "HackrfProductLiveFailure", "HackrfProductLiveSnapshot",
    "HackrfProductLiveStartResult", "HackrfProductLiveState",
    "HackrfProductLiveStopResult", "HackrfRuntimeControlPort",
    "admit_hackrf_live",
    "TinySaComposedSource", "TinySaIdentityAssurance",
    "TinySaSerialSettingsCommandPort", "TinySaSerialSourceBackend",
    "TinySaSourceCandidate", "TinySaSourceCompositionError",
    "TinySaSourceCompositionService", "TinySaSourcePhase",
    "TinySaSourceReason", "TinySaSourceSnapshot", "TinySaVerifiedSource"]
