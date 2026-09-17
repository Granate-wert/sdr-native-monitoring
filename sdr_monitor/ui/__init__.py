"""Standalone UI package with lazy Qt imports.

Scale, validation and display-policy modules remain importable in headless
build/test environments.  Qt widgets are imported only when an exported UI
symbol is actually requested.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "SDRAppShell": (".app_shell", "SDRAppShell"),
    "WorkspaceId": (".app_shell", "WorkspaceId"),
    "OptionalWorkspaceId": (".app_shell", "OptionalWorkspaceId"),
    "HackrfActivationWorkspaceRegistration": (".hackrf_activation_registration", "HackrfActivationWorkspaceRegistration"),
    "TinySaAnalyzerWorkspaceRegistration": (".tinysa_analyzer_registration", "TinySaAnalyzerWorkspaceRegistration"),
    "TinySaSourceActivationDialog": (".dialogs.tinysa_source_activation", "TinySaSourceActivationDialog"),
    "AppliedValueRow": (".components", "AppliedValueRow"),
    "EmptyState": (".components", "EmptyState"),
    "ErrorState": (".components", "ErrorState"),
    "FrequencyInput": (".components", "FrequencyInput"),
    "MeasurementCard": (".components", "MeasurementCard"),
    "NumericReadout": (".components", "NumericReadout"),
    "SectionCard": (".components", "SectionCard"),
    "StatusChip": (".components", "StatusChip"),
    "TaskProgress": (".components", "TaskProgress"),
    "DesignTokens": (".design_tokens", "DesignTokens"),
    "StatusTone": (".design_tokens", "StatusTone"),
    "ThemeId": (".design_tokens", "ThemeId"),
    "ThemeProvider": (".themes", "ThemeProvider"),
    "InteractiveHeatBar": (".heatbar", "InteractiveHeatBar"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
