"""Externally composed UI V2 workspaces; none owns a service or receiver."""

from .calibration_profiles import CalibrationProfilesWorkspaceV2, calibration_profiles_workspace_definition
from .diagnostics import DiagnosticsWorkspaceV2, diagnostics_workspace_definition
from .home import HomeWorkspaceV2, home_workspace_definition
from .live import LiveWorkspaceV2, live_workspace_definition
from .replay import ReplayWorkspaceV2, replay_workspace_definition
from .sweep import SweepWorkspaceV2, sweep_workspace_definition
from .tinysa_activation import TinySaActivationWorkspaceV2, tinysa_activation_workspace_definition
from .tinysa_analyzer import TinySaAnalyzerWorkspaceV2, tinysa_analyzer_workspace_definition

__all__ = [
    "CalibrationProfilesWorkspaceV2",
    "DiagnosticsWorkspaceV2",
    "HomeWorkspaceV2",
    "LiveWorkspaceV2",
    "ReplayWorkspaceV2",
    "SweepWorkspaceV2",
    "TinySaActivationWorkspaceV2",
    "TinySaAnalyzerWorkspaceV2",
    "calibration_profiles_workspace_definition",
    "diagnostics_workspace_definition",
    "home_workspace_definition",
    "live_workspace_definition",
    "replay_workspace_definition",
    "sweep_workspace_definition",
    "tinysa_activation_workspace_definition",
    "tinysa_analyzer_workspace_definition",
]
