"""Typed presentation contracts for the incremental P16 UI migration."""

from .app_shell import (
    DEFAULT_GEOMETRY,
    MINIMUM_GEOMETRY,
    AppShell,
    ROLE_BOTTOM_TOOLS,
    ROLE_CONTEXT_INSPECTOR,
    ROLE_HEALTH_BAR,
    ROLE_NAVIGATION_RAIL,
    ROLE_PLACEHOLDER,
    ROLE_SOURCE_NAVIGATOR,
    ROLE_WORKSPACE_HOST,
    ShellPresetSnapshot,
    build_shell_command_registry,
    shell_placeholder_widget,
)
from .bootstrap import (
    BootstrapConfig,
    TRUTHY_FALSE_VALUES,
    USE_APP_SHELL_ENV,
    build_application_window,
    configure_application_identity,
    resolve_bootstrap_config,
)
from .commands import CommandRegistry, CommandSpec
from .components import FrequencyInput, ReadOnlyValue, StatusBadge
from .design_tokens import COLOR_BLIND_SCIENTIFIC_PALETTES, DesignTokens, StatusTone, ThemeId
from .icons import IconId, IconRegistry
from .identity import CURRENT_IDENTITY, DEFAULT_LEGACY_SCOPE, LegacySettingsScope, ProductIdentity
from .layout_presets import LayoutPreset, LayoutPresetCatalog, LayoutPresetId, PresetArea
from .live_dialog import DeviceDiscoveryDialog, discovery_dialog_callback
from .live_discovery import (
    DeviceKind,
    DiscoveredDevice,
    DiscoveryError,
    discover_devices,
    parse_manual_uri,
)
from .live_presenter import LiveMonitorPresenter, default_backend_availability
from .calibration_presenter import CalibrationPresenter
from .calibration_state import CalibrationComparisonRow, CalibrationImportPreview, CalibrationPlotSnapshot, CalibrationProfileSnapshot, CalibrationWorkspaceSnapshot
from .calibration_workspace import CalibrationPlot, CalibrationWorkspace
from .measurement_presenter import MeasurementPresenter
from .measurement_state import MeasurementCardSnapshot, MeasurementWorkspaceSnapshot
from .measurements_panel import MeasurementPanel
from .recording_presenter import RecordingPresenter
from .recording_state import RecordingWorkspaceSnapshot, ReplaySourceKind
from .recording_workspace import RecordingWorkspace
from .diagnostics_presenter import DiagnosticsPresenter
from .diagnostics_state import DiagnosticsSnapshot, ValidationRunState
from .diagnostics_workspace import DiagnosticsWorkspace
from .live_state import (
    BackendBadge,
    CalibrationBadge,
    LiveMonitorSnapshot,
    QualityFlagItem,
    RecordingHookState,
    RequestedAppliedValue,
)
from .live_workspace import LiveMonitorWorkspace
from .notifications import NotificationItem, NotificationSeverity
from .offline_presenter import OfflineDflPresenter
from .offline_state import (
    OfflineHeatmapSnapshot,
    OfflineMarkerSnapshot,
    OfflinePlaybackSnapshot,
    OfflineResultSnapshot,
    OfflineSessionSnapshot,
    OfflineStatusSnapshot,
    OfflineTraceSnapshot,
    OfflineWaterfallSnapshot,
    OfflineWorkspaceSnapshot,
)
from .offline_workspace import OfflineDflWorkspace
from .presenters import Presenter, PresenterCoordinator
from .services import ApplicationServices
from .settings_migration import (
    FRAME_NAV_KEYS,
    LegacySettings,
    MigrationResult,
    THEME_KEY,
    apply_migration,
    legacy_settings_are_readable,
    open_legacy_settings,
    read_legacy_settings,
)
from .state import AppUiState, UiUpdateBatch, WorkspaceId
from .sweep_presenter import SweepPresenter, SweepServiceFactory
from .sweep_state import (
    SweepPlanSegmentSnapshot,
    SweepPlanSnapshot,
    SweepQualitySnapshot,
    SweepResultSnapshot,
    SweepRunSnapshot,
    SweepRunStatus,
    SweepSeamSnapshot,
    SweepWorkspaceSnapshot,
)
from .sweep_workspace import SweepWorkspace
from .themes import ThemeOption, ThemeProvider
from .units import format_frequency_hz, format_level, parse_frequency_hz, parse_localized_number
from .workspace_registry import (
    PlaceholderFactory,
    WorkspaceDescriptor,
    WorkspacePlaceholder,
    WorkspaceRegistry,
)

__all__ = [
    "AppShell",
    "AppUiState",
    "ApplicationServices",
    "BackendBadge",
    "COLOR_BLIND_SCIENTIFIC_PALETTES",
    "BootstrapConfig",
    "CalibrationBadge",
    "CalibrationComparisonRow",
    "CalibrationImportPreview",
    "CalibrationPlot",
    "CalibrationPlotSnapshot",
    "CalibrationPresenter",
    "CalibrationProfileSnapshot",
    "CalibrationWorkspace",
    "CalibrationWorkspaceSnapshot",
    "MeasurementCardSnapshot",
    "MeasurementPanel",
    "MeasurementPresenter",
    "RecordingPresenter",
    "RecordingWorkspace",
    "RecordingWorkspaceSnapshot",
    "ReplaySourceKind",
    "DiagnosticsPresenter",
    "DiagnosticsWorkspace",
    "DiagnosticsSnapshot",
    "ValidationRunState",
    "MeasurementWorkspaceSnapshot",
    "CommandRegistry",
    "CommandSpec",
    "CURRENT_IDENTITY",
    "DEFAULT_GEOMETRY",
    "DEFAULT_LEGACY_SCOPE",
    "DesignTokens",
    "DeviceDiscoveryDialog",
    "DeviceKind",
    "DiscoveredDevice",
    "DiscoveryError",
    "FRAME_NAV_KEYS",
    "FrequencyInput",
    "IconId",
    "IconRegistry",
    "LayoutPreset",
    "LayoutPresetCatalog",
    "LayoutPresetId",
    "LegacySettings",
    "LegacySettingsScope",
    "LiveMonitorPresenter",
    "LiveMonitorSnapshot",
    "LiveMonitorWorkspace",
    "MINIMUM_GEOMETRY",
    "MigrationResult",
    "NotificationItem",
    "NotificationSeverity",
    "OfflineDflPresenter",
    "OfflineDflWorkspace",
    "OfflineHeatmapSnapshot",
    "OfflineMarkerSnapshot",
    "OfflinePlaybackSnapshot",
    "OfflineResultSnapshot",
    "OfflineSessionSnapshot",
    "OfflineStatusSnapshot",
    "OfflineTraceSnapshot",
    "OfflineWaterfallSnapshot",
    "OfflineWorkspaceSnapshot",
    "PlaceholderFactory",
    "PresetArea",
    "Presenter",
    "PresenterCoordinator",
    "ProductIdentity",
    "QualityFlagItem",
    "ROLE_BOTTOM_TOOLS",
    "ROLE_CONTEXT_INSPECTOR",
    "ROLE_HEALTH_BAR",
    "ROLE_NAVIGATION_RAIL",
    "ROLE_PLACEHOLDER",
    "ROLE_SOURCE_NAVIGATOR",
    "ROLE_WORKSPACE_HOST",
    "ReadOnlyValue",
    "RecordingHookState",
    "RequestedAppliedValue",
    "ShellPresetSnapshot",
    "StatusBadge",
    "StatusTone",
    "SweepPlanSegmentSnapshot",
    "SweepPlanSnapshot",
    "SweepPresenter",
    "SweepQualitySnapshot",
    "SweepResultSnapshot",
    "SweepRunSnapshot",
    "SweepRunStatus",
    "SweepSeamSnapshot",
    "SweepServiceFactory",
    "SweepWorkspace",
    "SweepWorkspaceSnapshot",
    "THEME_KEY",
    "TRUTHY_FALSE_VALUES",
    "ThemeId",
    "ThemeOption",
    "ThemeProvider",
    "UiUpdateBatch",
    "USE_APP_SHELL_ENV",
    "WorkspaceDescriptor",
    "WorkspaceId",
    "WorkspacePlaceholder",
    "WorkspaceRegistry",
    "apply_migration",
    "build_application_window",
    "build_shell_command_registry",
    "configure_application_identity",
    "default_backend_availability",
    "discover_devices",
    "discovery_dialog_callback",
    "format_frequency_hz",
    "format_level",
    "legacy_settings_are_readable",
    "open_legacy_settings",
    "parse_frequency_hz",
    "parse_localized_number",
    "parse_manual_uri",
    "read_legacy_settings",
    "resolve_bootstrap_config",
    "shell_placeholder_widget",
]
