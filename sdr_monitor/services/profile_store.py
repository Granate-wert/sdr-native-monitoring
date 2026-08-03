"""Atomic local storage for standalone live-monitor profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..domain.profiles import LiveProfile


class LiveProfileStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> tuple[LiveProfile, ...]:
        if not self._path.exists():
            return ()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return tuple(LiveProfile.from_dict(item) for item in payload.get("profiles", ()))

    def save(self, profiles: tuple[LiveProfile, ...]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".part")
        temporary.write_text(json.dumps({"version": 1, "profiles": [profile.to_dict() for profile in profiles]}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self._path)

    def upsert(self, profile: LiveProfile) -> tuple[LiveProfile, ...]:
        updated = tuple(item for item in self.load() if item.profile_id != profile.profile_id) + (profile,)
        self.save(updated)
        return updated
