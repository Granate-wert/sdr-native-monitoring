"""Typed presentation contracts for the incremental P16 UI migration.

This package deliberately contains no device, DSP, parser, or renderer logic.
Legacy widgets access existing application services through compatibility adapters.
"""

from .commands import CommandRegistry, CommandSpec
from .notifications import NotificationItem, NotificationSeverity
from .presenters import Presenter, PresenterCoordinator
from .services import ApplicationServices
from .state import AppUiState, UiUpdateBatch, WorkspaceId

__all__ = [
    "AppUiState",
    "ApplicationServices",
    "CommandRegistry",
    "CommandSpec",
    "NotificationItem",
    "NotificationSeverity",
    "Presenter",
    "PresenterCoordinator",
    "UiUpdateBatch",
    "WorkspaceId",
]
