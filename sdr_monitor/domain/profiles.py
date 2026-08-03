"""Persistable live-monitor profiles without device secrets or raw samples."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .live import BackendKind, LiveConfiguration


@dataclass(frozen=True, slots=True)
class LiveProfile:
    profile_id: str
    title: str
    configuration: LiveConfiguration

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self.configuration)
        payload["backend"] = self.configuration.backend.value
        return {"profile_id": self.profile_id, "title": self.title, "configuration": payload}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LiveProfile":
        configuration = dict(payload["configuration"])
        configuration["backend"] = BackendKind(configuration["backend"])
        return cls(profile_id=str(payload["profile_id"]), title=str(payload["title"]), configuration=LiveConfiguration(**configuration))
