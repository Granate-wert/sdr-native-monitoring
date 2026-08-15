"""Qt-free explicit tinySA source activation use cases."""

from __future__ import annotations

from typing import Protocol

from ..services.tinysa_source_composition import (
    TinySaComposedSource,
    TinySaSourceCompositionService,
    TinySaSourceSnapshot,
    TinySaVerifiedSource,
)


class TinySaSourceActivationUseCases(Protocol):
    def current(self) -> TinySaSourceSnapshot: ...

    def discover(self) -> TinySaSourceSnapshot: ...

    def select(self, source_id: str) -> TinySaSourceSnapshot: ...

    def verify_selected(self) -> TinySaVerifiedSource: ...

    def compose_selected(self) -> TinySaComposedSource: ...


class TinySaSourceActivationApplicationService:
    """Expose only explicit R11-AA transitions to a future presenter."""

    def __init__(self, composition: TinySaSourceCompositionService) -> None:
        if not isinstance(composition, TinySaSourceCompositionService):
            raise TypeError("tinySA activation requires the R11-AA composition service")
        self._composition = composition

    def current(self) -> TinySaSourceSnapshot:
        return self._composition.current()

    def discover(self) -> TinySaSourceSnapshot:
        return self._composition.discover()

    def select(self, source_id: str) -> TinySaSourceSnapshot:
        return self._composition.select(source_id)

    def verify_selected(self) -> TinySaVerifiedSource:
        return self._composition.verify_selected()

    def compose_selected(self) -> TinySaComposedSource:
        return self._composition.compose_selected()


__all__ = [
    "TinySaSourceActivationApplicationService",
    "TinySaSourceActivationUseCases",
]
