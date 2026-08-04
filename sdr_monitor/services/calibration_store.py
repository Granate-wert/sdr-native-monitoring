"""Atomic, immutable calibration profile storage for standalone SDR."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..domain.calibration import CalibrationProfile, CalibrationProfileError


class CalibrationProfileStore:
    """Stores finalized versions without overwriting an existing version."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> tuple[CalibrationProfile, ...]:
        profiles: list[CalibrationProfile] = []
        for path in sorted(self.root.glob("*/v*.json")):
            try:
                profiles.append(CalibrationProfile.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, CalibrationProfileError, TypeError, ValueError):
                continue
        return tuple(sorted(profiles, key=lambda item: (item.profile_id, item.profile_version)))

    def load(self, profile_id: str, profile_version: int) -> CalibrationProfile:
        path = self._path(profile_id, profile_version)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CalibrationProfileError(f"calibration profile is unavailable: {profile_id} v{profile_version}") from error
        return CalibrationProfile.from_dict(payload)

    def save(self, profile: CalibrationProfile) -> CalibrationProfile:
        path = self._path(profile.profile_id, profile.profile_version)
        if path.exists():
            existing = self.load(profile.profile_id, profile.profile_version)
            if existing.fingerprint != profile.fingerprint:
                raise CalibrationProfileError("immutable calibration version already contains different data")
            return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(path.suffix + ".part")
        part.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(part, path)
        return profile

    def _path(self, profile_id: str, profile_version: int) -> Path:
        if not profile_id or any(char in profile_id for char in "\\/:") or profile_version <= 0:
            raise CalibrationProfileError("invalid calibration profile key")
        return self.root / profile_id / f"v{profile_version:04d}.json"


__all__ = ["CalibrationProfileStore"]
