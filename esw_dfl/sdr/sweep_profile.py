"""JSON-backed sweep profile presets for the Wideband Sweep workspace.

Each profile combines immutable :class:`SweepConfig` and
:class:`SweepPlannerOptions` values with a display name.  Persistence uses a
versioned JSON schema and atomic sibling ``.part`` writes, mirroring
``live_profile.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths

from .contracts import SweepConfig
from .sweep import SweepPlannerOptions


_PROFILE_SCHEMA_NAME: str = "sdr_sweep_profiles"
_PROFILE_SCHEMA_VERSION: int = 1


def _sweep_config_to_dict(config: SweepConfig) -> dict[str, Any]:
    """Serialize an immutable sweep configuration for profile storage."""

    return {
        "start_frequency_hz": config.start_frequency_hz,
        "stop_frequency_hz": config.stop_frequency_hz,
        "sample_rate_hz": config.sample_rate_hz,
        "analog_bandwidth_hz": config.analog_bandwidth_hz,
        "overlap_hz": config.overlap_hz,
        "fft_size": config.fft_size,
        "hop_size": config.hop_size,
        "dwell_frames": config.dwell_frames,
        "settling_time_seconds": config.settling_time_seconds,
        "discard_blocks": config.discard_blocks,
        "schema_version": config.schema_version,
    }


def _sweep_config_from_dict(payload: Mapping[str, Any]) -> SweepConfig:
    """Rebuild a validated :class:`SweepConfig` from persisted fields."""

    return SweepConfig(
        start_frequency_hz=float(payload["start_frequency_hz"]),
        stop_frequency_hz=float(payload["stop_frequency_hz"]),
        sample_rate_hz=float(payload["sample_rate_hz"]),
        analog_bandwidth_hz=float(payload["analog_bandwidth_hz"]),
        overlap_hz=float(payload["overlap_hz"]),
        fft_size=int(payload["fft_size"]),
        hop_size=int(payload["hop_size"]),
        dwell_frames=int(payload["dwell_frames"]),
        settling_time_seconds=float(payload["settling_time_seconds"]),
        discard_blocks=int(payload["discard_blocks"]),
        schema_version=int(payload["schema_version"]),
    )


def _options_to_dict(options: SweepPlannerOptions) -> dict[str, Any]:
    """Serialize sweep crop planner options for profile storage."""

    return {
        "edge_margin_hz": options.edge_margin_hz,
        "dc_exclusion_hz": options.dc_exclusion_hz,
    }


def _options_from_dict(payload: Mapping[str, Any]) -> SweepPlannerOptions:
    """Rebuild validated :class:`SweepPlannerOptions` from persisted fields."""

    return SweepPlannerOptions(
        edge_margin_hz=float(payload["edge_margin_hz"]),
        dc_exclusion_hz=float(payload["dc_exclusion_hz"]),
    )


@dataclass(frozen=True, slots=True)
class SweepProfile:
    """Immutable named sweep configuration preset."""

    profile_id: str
    display_name: str
    config: SweepConfig
    options: SweepPlannerOptions
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not isinstance(self.config, SweepConfig):
            raise TypeError("config must be SweepConfig")
        if not isinstance(self.options, SweepPlannerOptions):
            raise TypeError("options must be SweepPlannerOptions")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this profile to the store JSON shape."""

        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "config": _sweep_config_to_dict(self.config),
            "options": _options_to_dict(self.options),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SweepProfile:
        """Parse one profile from the store JSON shape."""

        config_payload = payload.get("config")
        options_payload = payload.get("options")
        if not isinstance(config_payload, Mapping):
            raise ValueError("profile payload missing 'config' mapping")
        if not isinstance(options_payload, Mapping):
            raise ValueError("profile payload missing 'options' mapping")
        return cls(
            profile_id=str(payload.get("profile_id", "")),
            display_name=str(payload.get("display_name", "")),
            config=_sweep_config_from_dict(config_payload),
            options=_options_from_dict(options_payload),
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True, slots=True)
class SweepProfileCollection:
    """A versioned, ordered collection of :class:`SweepProfile` entries."""

    schema_name: str
    schema_version: int
    profiles: tuple[SweepProfile, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.schema_name != _PROFILE_SCHEMA_NAME:
            raise ValueError(f"unsupported schema_name: {self.schema_name!r}")
        if self.schema_version != _PROFILE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version!r}")

    def find(self, profile_id: str) -> SweepProfile | None:
        """Return the profile with an exact identifier, if present."""

        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete versioned profile collection."""

        return {
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "profiles": [profile.to_dict() for profile in self.profiles],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SweepProfileCollection:
        """Parse a versioned sweep profile collection."""

        profiles_payload = payload.get("profiles", ())
        if not isinstance(profiles_payload, list):
            raise ValueError("profiles payload must be a list")
        profiles = tuple(
            SweepProfile.from_dict(item)
            for item in profiles_payload
            if isinstance(item, Mapping)
        )
        return cls(
            schema_name=str(payload.get("schema_name", _PROFILE_SCHEMA_NAME)),
            schema_version=int(payload.get("schema_version", _PROFILE_SCHEMA_VERSION)),
            profiles=profiles,
        )


class SweepProfileStore:
    """JSON-backed atomic persistence for :class:`SweepProfileCollection`."""

    _FILE_NAME: str = "sweep_profiles.json"

    def __init__(self, base_directory: Path | None = None) -> None:
        self._base_directory: Path = (
            Path(base_directory)
            if base_directory is not None
            else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        )

    @property
    def file_path(self) -> Path:
        """Return the collection path under the configured base directory."""

        return self._base_directory / self._FILE_NAME

    def load(self) -> SweepProfileCollection:
        """Load one collection, returning an empty one for missing or empty files."""

        path = self.file_path
        if not path.exists():
            return SweepProfileCollection(_PROFILE_SCHEMA_NAME, _PROFILE_SCHEMA_VERSION)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise SweepProfileError(f"unable to read {path}: {error}") from error
        if not raw.strip():
            return SweepProfileCollection(_PROFILE_SCHEMA_NAME, _PROFILE_SCHEMA_VERSION)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SweepProfileError(f"invalid JSON in {path}: {error.msg}") from error
        if not isinstance(payload, dict):
            raise SweepProfileError(f"profile file {path} must contain a JSON object")
        try:
            return SweepProfileCollection.from_dict(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise SweepProfileError(f"profile file {path} is malformed: {error}") from error

    def save(self, profiles: Iterable[SweepProfile]) -> SweepProfileCollection:
        """Persist profiles atomically and return their versioned collection."""

        collection = SweepProfileCollection(
            schema_name=_PROFILE_SCHEMA_NAME,
            schema_version=_PROFILE_SCHEMA_VERSION,
            profiles=tuple(profiles),
        )
        path = self.file_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SweepProfileError(f"unable to create {path.parent}: {error}") from error
        payload = json.dumps(collection.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            temporary.write_text(payload + "\n", encoding="utf-8")
        except OSError as error:
            raise SweepProfileError(f"unable to write {temporary}: {error}") from error
        try:
            temporary.replace(path)
        except OSError as error:
            raise SweepProfileError(f"unable to commit {path}: {error}") from error
        return collection

    def is_writable(self) -> bool:
        """Return whether the profile base directory can be created."""

        try:
            self._base_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return True


class SweepProfileError(ValueError):
    """Raised when a :class:`SweepProfileStore` operation fails."""


__all__ = ["SweepProfile", "SweepProfileCollection", "SweepProfileError", "SweepProfileStore"]
