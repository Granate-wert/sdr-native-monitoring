"""Explicit UI-only registration for the inert tinySA analyzer workspace."""

from __future__ import annotations

from dataclasses import dataclass

from ..application.tinysa_analyzer import TinySaAnalyzerApplicationService
from ..services.tinysa_source_composition import TinySaComposedSource, TinySaVerifiedSource
from .presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenter
from .workspaces.tinysa_analyzer import TinySaAnalyzerWorkspace


@dataclass(frozen=True, slots=True)
class TinySaAnalyzerWorkspaceRegistration:
    """Hand an already-composed presenter to the shell without device action."""

    presenter: TinySaAnalyzerPresenter
    source: TinySaVerifiedSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.presenter, TinySaAnalyzerPresenter):
            raise TypeError("tinySA analyzer registration requires an R11-Z presenter")
        if self.source is not None and not isinstance(self.source, TinySaVerifiedSource):
            raise TypeError("tinySA analyzer source binding is invalid")

    @classmethod
    def from_composed_source(
        cls,
        composed: TinySaComposedSource,
    ) -> TinySaAnalyzerWorkspaceRegistration:
        """Create the application/presenter hand-off without invoking either port."""

        if not isinstance(composed, TinySaComposedSource):
            raise TypeError("tinySA analyzer requires an R11-AA composed source")
        use_cases = TinySaAnalyzerApplicationService(
            composed.collector,
            composed.settings_executor,
        )
        return cls(TinySaAnalyzerPresenter(use_cases), composed.verified)

    def create_workspace(self) -> TinySaAnalyzerWorkspace:
        """Create an inert workspace; collection remains an explicit UI action."""

        workspace = TinySaAnalyzerWorkspace(self.presenter)
        if self.source is not None:
            workspace.set_source_identity(self.source)
        return workspace


__all__ = ["TinySaAnalyzerWorkspaceRegistration"]
