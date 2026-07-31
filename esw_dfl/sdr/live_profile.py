"""Typed device profiles used by the Live Monitor workspace.

A :class:`DeviceProfile` is a small immutable dataclass that bundles the
low-rate identity of a Pluto session with one :class:`FixedBandOptions`
snapshot.  The shape deliberately mirrors the fields a user sees in the
Basic/Expert controls of the Live Monitor workspace; the same dataclass
can be exported to JSON, re-imported on the next launch and used to
construct a :class:`LiveSessionConfig` without recomputing defaults.

The persistence layer (:class:`DeviceProfileStore`) keeps one profile
collection per :class:`DeviceProfileStore`.  Every write goes through
``QStandardPaths.AppDataLocation`` so the path follows the host OS
conventions and respects the same parent directory as the application
``QSettings`` store.

Profiles never contain raw I/Q samples, device serials, network
identifiers or calibration secrets.  Only the public Pluto URI, the
display name and the requested options are persisted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths

from .contracts import (
    CalibrationStatus,
    ComputeBackendKind,
    DetectorType,
    DeviceConfig,
    DspConfig,
    GainMode,
    OverflowPolicy,
    PersistenceConfig,
    PersistenceMode,
    PrecisionMode,
    SpectrumUnit,
    WindowType,
)
from .fixed_band import FixedBandOptions


_PROFILE_SCHEMA_NAME: str = "sdr_native_monitoring.device_profile"
_PROFILE_SCHEMA_VERSION: int = 1


def _enum_name(enum_type: type[Any], value: Any) -> str:
    if not isinstance(value, enum_type):
        raise TypeError(f"expected {enum_type.__name__}, got {type(value).__name__}")
    return value.name


def _enum_by_name(enum_type: type[Any], name: str) -> Any:
    try:
        return enum_type[name]
    except KeyError as error:
        raise ValueError(f"unknown {enum_type.__name__} member: {name!r}") from error


def _fixed_band_options_to_dict(options: FixedBandOptions) -> dict[str, Any]:
    """Serialize :class:`FixedBandOptions` without touching the contract layer."""

    device = options.device
    dsp = options.dsp
    persistence = options.persistence
    return {
        "device": {
            "source_id": device.source_id,
            "context_uri": device.context_uri,
            "center_frequency_hz": device.center_frequency_hz,
            "sample_rate_hz": device.sample_rate_hz,
            "analog_bandwidth_hz": device.analog_bandwidth_hz,
            "gain_mode": _enum_name(GainMode, device.gain_mode),
            "manual_gain_db": device.manual_gain_db,
            "channel_index": device.channel_index,
            "buffer_samples": device.buffer_samples,
        },
        "dsp": {
            "fft_size": dsp.fft_size,
            "hop_size": dsp.hop_size,
            "window": _enum_name(WindowType, dsp.window),
            "detector": _enum_name(type(dsp.detector), dsp.detector),
            "unit": _enum_name(SpectrumUnit, dsp.unit),
            "precision_mode": _enum_name(PrecisionMode, dsp.precision_mode),
            "batch_size": dsp.batch_size,
            "averaging_frames": dsp.averaging_frames,
            "kaiser_beta": dsp.kaiser_beta,
            "calibration_status": _enum_name(CalibrationStatus, dsp.calibration_status),
            "calibration_profile_id": dsp.calibration_profile_id,
        },
        "persistence": {
            "enabled": persistence.enabled,
            "mode": _enum_name(PersistenceMode, persistence.mode),
            "window_frames": persistence.window_frames,
            "half_life_seconds": persistence.half_life_seconds,
            "power_min_db": persistence.power_min_db,
            "power_max_db": persistence.power_max_db,
            "power_bins": persistence.power_bins,
            "snapshot_rate_hz": persistence.snapshot_rate_hz,
        },
        "backend": _enum_name(ComputeBackendKind, options.backend),
        "allow_runtime_fallback": options.allow_runtime_fallback,
        "acquisition_queue_capacity": options.acquisition_queue_capacity,
        "acquisition_overflow": _enum_name(OverflowPolicy, options.acquisition_overflow),
        "spectrum_queue_capacity": options.spectrum_queue_capacity,
        "event_queue_capacity": options.event_queue_capacity,
        "snapshot_rate_hz": options.snapshot_rate_hz,
        "discard_blocks_after_start": options.discard_blocks_after_start,
        "dc_removal_block_mean": options.dc_removal_block_mean,
    }


def _fixed_band_options_from_dict(payload: Mapping[str, Any]) -> FixedBandOptions:
    """Rebuild :class:`FixedBandOptions` from the dict shape of :func:`_fixed_band_options_to_dict`."""

    device = payload["device"]
    dsp = payload["dsp"]
    persistence = payload["persistence"]
    if (
        not isinstance(device, Mapping)
        or not isinstance(dsp, Mapping)
        or not isinstance(persistence, Mapping)
    ):
        raise ValueError("options payload must contain device/dsp/persistence mappings")
    device_config = DeviceConfig(
        source_id=str(device["source_id"]),
        context_uri=str(device["context_uri"]),
        center_frequency_hz=float(device["center_frequency_hz"]),
        sample_rate_hz=float(device["sample_rate_hz"]),
        analog_bandwidth_hz=float(device["analog_bandwidth_hz"]),
        gain_mode=_enum_by_name(GainMode, str(device["gain_mode"])),
        manual_gain_db=float(device["manual_gain_db"]),
        channel_index=int(device["channel_index"]),
        buffer_samples=int(device["buffer_samples"]),
    )
    dsp_config = DspConfig(
        fft_size=int(dsp["fft_size"]),
        hop_size=int(dsp["hop_size"]),
        window=_enum_by_name(WindowType, str(dsp["window"])),
        detector=_enum_by_name(DetectorType, str(dsp["detector"])),
        unit=_enum_by_name(SpectrumUnit, str(dsp["unit"])),
        precision_mode=_enum_by_name(PrecisionMode, str(dsp["precision_mode"])),
        batch_size=int(dsp["batch_size"]),
        averaging_frames=int(dsp["averaging_frames"]),
        kaiser_beta=float(dsp["kaiser_beta"]),
        calibration_status=_enum_by_name(CalibrationStatus, str(dsp["calibration_status"])),
        calibration_profile_id=(
            None if dsp.get("calibration_profile_id") is None else str(dsp["calibration_profile_id"])
        ),
    )
    persistence_config = PersistenceConfig(
        enabled=bool(persistence["enabled"]),
        mode=_enum_by_name(PersistenceMode, str(persistence["mode"])),
        window_frames=int(persistence["window_frames"]),
        half_life_seconds=float(persistence["half_life_seconds"]),
        power_min_db=float(persistence["power_min_db"]),
        power_max_db=float(persistence["power_max_db"]),
        power_bins=int(persistence["power_bins"]),
        snapshot_rate_hz=float(persistence["snapshot_rate_hz"]),
    )
    return FixedBandOptions(
        device=device_config,
        dsp=dsp_config,
        persistence=persistence_config,
        backend=_enum_by_name(ComputeBackendKind, str(payload["backend"])),
        allow_runtime_fallback=bool(payload["allow_runtime_fallback"]),
        acquisition_queue_capacity=int(payload["acquisition_queue_capacity"]),
        acquisition_overflow=_enum_by_name(OverflowPolicy, str(payload["acquisition_overflow"])),
        spectrum_queue_capacity=int(payload["spectrum_queue_capacity"]),
        event_queue_capacity=int(payload["event_queue_capacity"]),
        snapshot_rate_hz=float(payload["snapshot_rate_hz"]),
        discard_blocks_after_start=int(payload["discard_blocks_after_start"]),
        dc_removal_block_mean=bool(payload["dc_removal_block_mean"]),
    )


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Immutable Pluto profile: URI + display name + requested options."""

    profile_id: str
    display_name: str
    uri: str
    options: FixedBandOptions
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not self.uri.strip():
            raise ValueError("uri must not be empty")
        if not isinstance(self.options, FixedBandOptions):
            raise TypeError("options must be FixedBandOptions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "uri": self.uri,
            "options": _fixed_band_options_to_dict(self.options),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DeviceProfile:
        options_payload = payload.get("options")
        if not isinstance(options_payload, Mapping):
            raise ValueError("profile payload missing 'options' mapping")
        options = _fixed_band_options_from_dict(options_payload)
        return cls(
            profile_id=str(payload.get("profile_id", "")),
            display_name=str(payload.get("display_name", "")),
            uri=str(payload.get("uri", "")),
            options=options,
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True, slots=True)
class ProfileCollection:
    """A versioned, ordered collection of :class:`DeviceProfile` entries."""

    schema_name: str
    schema_version: int
    profiles: tuple[DeviceProfile, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.schema_name != _PROFILE_SCHEMA_NAME:
            raise ValueError(f"unsupported schema_name: {self.schema_name!r}")
        if self.schema_version != _PROFILE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version!r}")

    def find(self, profile_id: str) -> DeviceProfile | None:
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "profiles": [profile.to_dict() for profile in self.profiles],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProfileCollection:
        profiles_payload = payload.get("profiles", ())
        if not isinstance(profiles_payload, list):
            raise ValueError("profiles payload must be a list")
        profiles = tuple(
            DeviceProfile.from_dict(item)
            for item in profiles_payload
            if isinstance(item, Mapping)
        )
        return cls(
            schema_name=str(payload.get("schema_name", _PROFILE_SCHEMA_NAME)),
            schema_version=int(payload.get("schema_version", _PROFILE_SCHEMA_VERSION)),
            profiles=profiles,
        )


class DeviceProfileStore:
    """JSON-backed persistence for :class:`ProfileCollection`.

    The store never silently swallows I/O errors: callers receive either
    a populated collection, an empty collection (when the file is missing
    or empty), or a :class:`ProfileStoreError` that names the offending
    path.  Atomic writes use a sibling ``.part`` file followed by a
    replace so a crash mid-write never produces a partially valid file.
    """

    _FILE_NAME: str = "device_profiles.json"

    def __init__(self, base_directory: Path | None = None) -> None:
        self._base_directory: Path = (
            Path(base_directory)
            if base_directory is not None
            else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        )

    @property
    def file_path(self) -> Path:
        return self._base_directory / self._FILE_NAME

    def load(self) -> ProfileCollection:
        path = self.file_path
        if not path.exists():
            return ProfileCollection(_PROFILE_SCHEMA_NAME, _PROFILE_SCHEMA_VERSION)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ProfileStoreError(f"unable to read {path}: {error}") from error
        if not raw.strip():
            return ProfileCollection(_PROFILE_SCHEMA_NAME, _PROFILE_SCHEMA_VERSION)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProfileStoreError(f"invalid JSON in {path}: {error.msg}") from error
        if not isinstance(payload, dict):
            raise ProfileStoreError(f"profile file {path} must contain a JSON object")
        try:
            return ProfileCollection.from_dict(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise ProfileStoreError(f"profile file {path} is malformed: {error}") from error

    def save(self, profiles: Iterable[DeviceProfile]) -> ProfileCollection:
        collection = ProfileCollection(
            schema_name=_PROFILE_SCHEMA_NAME,
            schema_version=_PROFILE_SCHEMA_VERSION,
            profiles=tuple(profiles),
        )
        path = self.file_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ProfileStoreError(f"unable to create {path.parent}: {error}") from error
        payload = json.dumps(collection.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            temporary.write_text(payload + "\n", encoding="utf-8")
        except OSError as error:
            raise ProfileStoreError(f"unable to write {temporary}: {error}") from error
        try:
            temporary.replace(path)
        except OSError as error:
            raise ProfileStoreError(f"unable to commit {path}: {error}") from error
        return collection

    def is_writable(self) -> bool:
        try:
            self._base_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return True


class ProfileStoreError(RuntimeError):
    """Raised when a :class:`DeviceProfileStore` operation fails."""


__all__ = [
    "DeviceProfile",
    "DeviceProfileStore",
    "ProfileCollection",
    "ProfileStoreError",
]
