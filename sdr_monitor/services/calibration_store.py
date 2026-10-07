"""Atomic, immutable calibration profile storage for standalone SDR."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..domain.calibration import (
    CalibrationProfile,
    CalibrationProfileError,
    validate_calibration_profile_id,
)


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
        # Each writer owns an exclusive temporary inode. Publish without
        # replacing a competitor's finalized version; existence checks alone
        # cannot enforce immutable versions across service/process instances.
        part: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                    prefix=path.name + ".", suffix=".part", delete=False) as stream:
                part = Path(stream.name)
                stream.write(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(part, path)
            except FileExistsError:
                existing = self.load(profile.profile_id, profile.profile_version)
                if existing.fingerprint != profile.fingerprint:
                    raise CalibrationProfileError("immutable calibration version already contains different data")
                return existing
            except OSError as error:
                # Filesystems without hard-link support fail closed. Never
                # fall back to overwrite or publish a partially written file.
                raise CalibrationProfileError("immutable calibration publication failed") from error
        finally:
            if part is not None:
                part.unlink(missing_ok=True)
        return profile

    def _path(self, profile_id: str, profile_version: int) -> Path:
        try:
            identifier = validate_calibration_profile_id(profile_id)
        except CalibrationProfileError as error:
            raise CalibrationProfileError("invalid calibration profile key") from error
        if (
            isinstance(profile_version, bool)
            or not isinstance(profile_version, int)
            or profile_version <= 0
        ):
            raise CalibrationProfileError("invalid calibration profile key")
        return self.root / identifier / f"v{profile_version:04d}.json"


__all__ = ["CalibrationProfileStore"]
