"""Explicit UI-only hand-off for an already admitted HackRF activation plan.

This module deliberately joins only the R11-M plan provenance and R11-R Qt
surface.  It has no default composition, vendor runtime or device action.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..services.hackrf_live_admission import (
    HackrfLiveActivationPlan,
    _is_issued_hackrf_live_activation_plan,
)
from .presenters.hackrf_activation_presenter import HackrfActivationPresenter
from .workspaces.hackrf_activation import HackrfActivationWorkspace


@dataclass(frozen=True, slots=True)
class HackrfActivationWorkspaceRegistration:
    """One explicit, already-admitted hand-off into an application shell.

    Validating the opaque R11-M issuer marker is intentionally the only work
    performed here.  Constructing a registration neither asks the R11-Q use
    cases to preflight nor creates the native factory or a receiver.
    """

    plan: HackrfLiveActivationPlan
    presenter: HackrfActivationPresenter

    def __post_init__(self) -> None:
        if not _is_issued_hackrf_live_activation_plan(self.plan):
            raise ValueError("HackRF activation registration requires an issued plan")
        if not isinstance(self.presenter, HackrfActivationPresenter):
            raise ValueError("HackRF activation registration requires an R11-R presenter")

    def create_workspace(self) -> HackrfActivationWorkspace:
        """Create the inert workspace without invoking an activation action."""

        workspace = HackrfActivationWorkspace(self.presenter)
        workspace.set_activation_plan(self.plan)
        return workspace


__all__ = ["HackrfActivationWorkspaceRegistration"]
