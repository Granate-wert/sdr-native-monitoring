"""Qt-free use cases for loading and selecting Live Monitor profiles."""

from __future__ import annotations

from typing import Protocol

from ..domain import LiveProfile


class LiveProfileNotFoundError(LookupError):
    """Raised when the requested profile is not present in the current store."""


class LiveProfilePort(Protocol):
    """Infrastructure read operations required by the bounded profile use case."""

    def load(self) -> tuple[LiveProfile, ...]: ...


class LiveProfileUseCases(Protocol):
    """Profile operations consumed by the Qt profile presenter."""

    def list_profiles(self) -> tuple[LiveProfile, ...]: ...
    def select_profile(self, profile_id: str) -> LiveProfile: ...


class LiveProfileApplicationService:
    """Resolves immutable profiles without Qt, widgets or storage details."""

    def __init__(self, port: LiveProfilePort) -> None:
        self._port = port

    def list_profiles(self) -> tuple[LiveProfile, ...]:
        profiles = tuple(self._port.load())
        identifiers = tuple(profile.profile_id for profile in profiles)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("live profile store contains duplicate profile_id values")
        return profiles

    def select_profile(self, profile_id: str) -> LiveProfile:
        requested = profile_id.strip()
        if not requested:
            raise LiveProfileNotFoundError("select a non-empty live profile id")
        for profile in self.list_profiles():
            if profile.profile_id == requested:
                return profile
        raise LiveProfileNotFoundError(f"unknown live profile: {requested}")


__all__ = [
    "LiveProfileApplicationService",
    "LiveProfileNotFoundError",
    "LiveProfilePort",
    "LiveProfileUseCases",
]
