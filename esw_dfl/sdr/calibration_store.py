"""P09 versioned calibration profiles and correction workflow.

This file is copied into the SDR Native Monitoring repository after patching.
The implementation is independent from Qt and from the native FFT loop:
profiles are loaded at the control-plane boundary and correction arrays are
prepared once for a frequency grid.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import numpy as np

from esw_dfl.sdr.contracts import CalibrationStatus, SpectrumUnit

CALIBRATION_SCHEMA_NAME = "sdr-calibration-profile"
CALIBRATION_SCHEMA_VERSION = 1
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CSV_COLUMNS = (
    "frequency_hz",
    "reference_dbm",
    "measured_dbfs",
    "correction_db",
    "uncertainty_db",
)


class CalibrationProfileError(ValueError):
    """Raised when a calibration profile is malformed or not applicable."""


class CalibrationApplicationStatus(StrEnum):
    CALIBRATED = "calibrated"
    INTERPOLATED = "interpolated"
    EXTRAPOLATED = "extrapolated"
    UNCALIBRATED = "uncalibrated"
    INVALID_FOR_SETTINGS = "invalid_for_settings"


CalibrationResultStatus = CalibrationApplicationStatus


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise CalibrationProfileError(f"{name} must be finite")
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CalibrationProfileError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise CalibrationProfileError(f"{name} must be finite")
    return result


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationProfileError(f"{name} must not be empty")
    return value.strip()


def _raw(payload: Mapping[str, object], name: str) -> Any:
    try:
        return payload[name]
    except KeyError as exc:
        raise CalibrationProfileError(f"missing {name}") from exc


def _same_float(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)


@dataclass(frozen=True, slots=True)
class CalibrationSignature:
    """Identity/settings tuple required by the profile applicability gate."""

    device_serial: str
    backend: str
    rf_port_path: str
    sample_rate_hz: float
    analog_bandwidth_hz: float
    gain_mode: str
    manual_gain_db: float
    window_normalization_version: str
    fft_unit_convention: str
    frontend_chain: str
    temperature_range_c: tuple[float, float] | None = None
    reference_plane: str = "rf_input"

    def __post_init__(self) -> None:
        for name in (
            "device_serial",
            "backend",
            "rf_port_path",
            "gain_mode",
            "window_normalization_version",
            "fft_unit_convention",
            "frontend_chain",
            "reference_plane",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("sample_rate_hz", "analog_bandwidth_hz", "manual_gain_db"):
            value = _finite(getattr(self, name), name)
            if name != "manual_gain_db" and value <= 0.0:
                raise CalibrationProfileError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.temperature_range_c is not None:
            if len(self.temperature_range_c) != 2:
                raise CalibrationProfileError("temperature_range_c must contain two values")
            low = _finite(self.temperature_range_c[0], "temperature_range_c[0]")
            high = _finite(self.temperature_range_c[1], "temperature_range_c[1]")
            if high < low:
                raise CalibrationProfileError("temperature range is reversed")
            object.__setattr__(self, "temperature_range_c", (low, high))

    def to_dict(self) -> dict[str, object]:
        return {
            "device_serial": self.device_serial,
            "backend": self.backend,
            "rf_port_path": self.rf_port_path,
            "sample_rate_hz": self.sample_rate_hz,
            "analog_bandwidth_hz": self.analog_bandwidth_hz,
            "gain_mode": self.gain_mode,
            "manual_gain_db": self.manual_gain_db,
            "window_normalization_version": self.window_normalization_version,
            "fft_unit_convention": self.fft_unit_convention,
            "frontend_chain": self.frontend_chain,
            "temperature_range_c": list(self.temperature_range_c) if self.temperature_range_c else None,
            "reference_plane": self.reference_plane,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CalibrationSignature":
        raw_temperature = payload.get("temperature_range_c")
        temperature: tuple[float, float] | None = None
        if raw_temperature is not None:
            if not isinstance(raw_temperature, (list, tuple)) or len(raw_temperature) != 2:
                raise CalibrationProfileError("temperature_range_c must be a two-item array or null")
            temperature = (_finite(raw_temperature[0], "temperature_range_c[0]"), _finite(raw_temperature[1], "temperature_range_c[1]"))
        try:
            return cls(
                device_serial=_raw(payload, "device_serial"),
                backend=_raw(payload, "backend"),
                rf_port_path=_raw(payload, "rf_port_path"),
                sample_rate_hz=_raw(payload, "sample_rate_hz"),
                analog_bandwidth_hz=_raw(payload, "analog_bandwidth_hz"),
                gain_mode=_raw(payload, "gain_mode"),
                manual_gain_db=_raw(payload, "manual_gain_db"),
                window_normalization_version=_raw(payload, "window_normalization_version"),
                fft_unit_convention=_raw(payload, "fft_unit_convention"),
                frontend_chain=_raw(payload, "frontend_chain"),
                temperature_range_c=temperature,
                reference_plane=cast(Any, payload.get("reference_plane", "rf_input")),
            )
        except KeyError as exc:
            raise CalibrationProfileError(f"signature is missing {exc.args[0]}") from exc


CalibrationSettings = CalibrationSignature


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    frequency_hz: float
    reference_dbm: float
    measured_dbfs: float
    correction_db: float
    uncertainty_db: float

    def __post_init__(self) -> None:
        for name in ("frequency_hz", "reference_dbm", "measured_dbfs", "correction_db", "uncertainty_db"):
            value = _finite(getattr(self, name), name)
            if name == "frequency_hz" and value <= 0.0:
                raise CalibrationProfileError("frequency_hz must be positive")
            if name == "uncertainty_db" and value < 0.0:
                raise CalibrationProfileError("uncertainty_db must not be negative")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, float]:
        return {
            "frequency_hz": self.frequency_hz,
            "reference_dbm": self.reference_dbm,
            "measured_dbfs": self.measured_dbfs,
            "correction_db": self.correction_db,
            "uncertainty_db": self.uncertainty_db,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CalibrationPoint":
        try:
            return cls(
                frequency_hz=_raw(payload, "frequency_hz"),
                reference_dbm=_raw(payload, "reference_dbm"),
                measured_dbfs=_raw(payload, "measured_dbfs"),
                correction_db=_raw(payload, "correction_db"),
                uncertainty_db=_raw(payload, "uncertainty_db"),
            )
        except KeyError as exc:
            raise CalibrationProfileError(f"point is missing {exc.args[0]}") from exc


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    correction_db: float
    uncertainty_db: float
    status: CalibrationApplicationStatus


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    """Finalized profile. A new calibration must use a new profile_version."""

    profile_id: str
    profile_version: int
    signature: CalibrationSignature
    reference_plane: str
    points: tuple[CalibrationPoint, ...]
    interpolation_method: str = "linear"
    valid_start_hz: float | None = None
    valid_stop_hz: float | None = None
    created_at: str = ""
    reference_equipment: str = ""
    notes: str = ""
    finalized: bool = True
    schema_version: int = CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        profile_id = _text(self.profile_id, "profile_id")
        if not _PROFILE_ID.fullmatch(profile_id):
            raise CalibrationProfileError("profile_id contains unsupported path characters")
        object.__setattr__(self, "profile_id", profile_id)
        if isinstance(self.profile_version, bool) or not isinstance(self.profile_version, int) or self.profile_version <= 0:
            raise CalibrationProfileError("profile_version must be a positive integer")
        if self.schema_version != CALIBRATION_SCHEMA_VERSION:
            raise CalibrationProfileError(f"unsupported calibration schema version {self.schema_version}")
        if self.interpolation_method != "linear":
            raise CalibrationProfileError("only linear calibration interpolation is supported")
        if not isinstance(self.signature, CalibrationSignature):
            raise CalibrationProfileError("signature must be CalibrationSignature")
        reference_plane = _text(self.reference_plane, "reference_plane")
        if reference_plane != self.signature.reference_plane:
            raise CalibrationProfileError("profile reference_plane must match signature reference_plane")
        object.__setattr__(self, "reference_plane", reference_plane)
        points = tuple(self.points)
        if len(points) < 2 or not all(isinstance(item, CalibrationPoint) for item in points):
            raise CalibrationProfileError("a profile requires at least two calibration points")
        if any(left.frequency_hz >= right.frequency_hz for left, right in zip(points, points[1:])):
            raise CalibrationProfileError("calibration points must be strictly increasing by frequency")
        object.__setattr__(self, "points", points)
        for name in ("valid_start_hz", "valid_stop_hz"):
            value = getattr(self, name)
            if value is not None:
                parsed = _finite(value, name)
                if parsed <= 0.0:
                    raise CalibrationProfileError(f"{name} must be positive")
                object.__setattr__(self, name, parsed)
        if self.valid_start_hz is not None and self.valid_stop_hz is not None and self.valid_stop_hz < self.valid_start_hz:
            raise CalibrationProfileError("valid_stop_hz must not be below valid_start_hz")
        created_at = self.created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        object.__setattr__(self, "created_at", _text(created_at, "created_at"))
        if not isinstance(self.finalized, bool) or not self.finalized:
            raise CalibrationProfileError("P09 store accepts only finalized immutable profiles")

    @property
    def frequency_points_hz(self) -> tuple[float, ...]:
        return tuple(point.frequency_hz for point in self.points)

    @property
    def correction_db(self) -> tuple[float, ...]:
        return tuple(point.correction_db for point in self.points)

    @property
    def uncertainty_db(self) -> tuple[float, ...]:
        return tuple(point.uncertainty_db for point in self.points)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _explicit_range_contains(self, frequency_hz: float) -> bool:
        return (
            (self.valid_start_hz is None or frequency_hz >= self.valid_start_hz)
            and (self.valid_stop_hz is None or frequency_hz <= self.valid_stop_hz)
        )

    def evaluate(self, frequency_hz: float, *, allow_extrapolation: bool = False) -> CalibrationSample:
        frequency = _finite(frequency_hz, "frequency_hz")
        if frequency <= 0.0 or not self._explicit_range_contains(frequency):
            return CalibrationSample(0.0, float("nan"), CalibrationApplicationStatus.INVALID_FOR_SETTINGS)
        frequencies = self.frequency_points_hz
        corrections = self.correction_db
        uncertainties = self.uncertainty_db
        exact = next((index for index, item in enumerate(frequencies) if frequency == item), None)
        if exact is not None:
            return CalibrationSample(corrections[exact], uncertainties[exact], CalibrationApplicationStatus.CALIBRATED)
        if frequency < frequencies[0] or frequency > frequencies[-1]:
            if not allow_extrapolation:
                return CalibrationSample(0.0, float("nan"), CalibrationApplicationStatus.INVALID_FOR_SETTINGS)
            left, right = (0, 1) if frequency < frequencies[0] else (len(frequencies) - 2, len(frequencies) - 1)
            status = CalibrationApplicationStatus.EXTRAPOLATED
        else:
            right = int(np.searchsorted(np.asarray(frequencies), frequency, side="right"))
            left = right - 1
            status = CalibrationApplicationStatus.INTERPOLATED
        fraction = (frequency - frequencies[left]) / (frequencies[right] - frequencies[left])
        correction = corrections[left] + fraction * (corrections[right] - corrections[left])
        uncertainty = uncertainties[left] + fraction * (uncertainties[right] - uncertainties[left])
        return CalibrationSample(float(correction), float(uncertainty), status)

    def prepare_grid(
        self,
        frequencies_hz: Sequence[float] | np.ndarray,
        *,
        allow_extrapolation: bool = False,
    ) -> "PreparedCalibration":
        frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
        if frequencies.size == 0 or not np.all(np.isfinite(frequencies)):
            raise CalibrationProfileError("frequency grid must be non-empty and finite")
        samples = tuple(self.evaluate(float(item), allow_extrapolation=allow_extrapolation) for item in frequencies)
        statuses = {sample.status for sample in samples}
        if CalibrationApplicationStatus.INVALID_FOR_SETTINGS in statuses:
            status = CalibrationApplicationStatus.INVALID_FOR_SETTINGS
        elif CalibrationApplicationStatus.EXTRAPOLATED in statuses:
            status = CalibrationApplicationStatus.EXTRAPOLATED
        elif CalibrationApplicationStatus.INTERPOLATED in statuses:
            status = CalibrationApplicationStatus.INTERPOLATED
        else:
            status = CalibrationApplicationStatus.CALIBRATED
        return PreparedCalibration(
            self.profile_id,
            self.profile_version,
            status,
            np.asarray([item.correction_db for item in samples], dtype=np.float64),
            np.asarray([item.uncertainty_db for item in samples], dtype=np.float64),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": CALIBRATION_SCHEMA_NAME,
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "finalized": self.finalized,
            "signature": self.signature.to_dict(),
            "reference_plane": self.reference_plane,
            "interpolation_method": self.interpolation_method,
            "valid_start_hz": self.valid_start_hz,
            "valid_stop_hz": self.valid_stop_hz,
            "created_at": self.created_at,
            "reference_equipment": self.reference_equipment,
            "notes": self.notes,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CalibrationProfile":
        if payload.get("schema") != CALIBRATION_SCHEMA_NAME:
            raise CalibrationProfileError("unknown calibration profile schema")
        if payload.get("schema_version") != CALIBRATION_SCHEMA_VERSION:
            raise CalibrationProfileError(f"unsupported calibration schema version {payload.get('schema_version')}")
        signature = payload.get("signature")
        raw_points = payload.get("points")
        if not isinstance(signature, Mapping) or not isinstance(raw_points, list):
            raise CalibrationProfileError("profile requires signature and points")
        try:
            points = tuple(CalibrationPoint.from_dict(cast(Mapping[str, object], item)) for item in raw_points)
            return cls(
                profile_id=_raw(payload, "profile_id"),
                profile_version=_raw(payload, "profile_version"),
                finalized=cast(Any, payload.get("finalized", True)),
                signature=CalibrationSignature.from_dict(signature),
                reference_plane=_raw(payload, "reference_plane"),
                interpolation_method=cast(Any, payload.get("interpolation_method", "linear")),
                valid_start_hz=cast(Any, payload.get("valid_start_hz")),
                valid_stop_hz=cast(Any, payload.get("valid_stop_hz")),
                created_at=cast(Any, payload.get("created_at", "")),
                reference_equipment=cast(Any, payload.get("reference_equipment", "")),
                notes=cast(Any, payload.get("notes", "")),
                points=points,
                schema_version=_raw(payload, "schema_version"),
            )
        except KeyError as exc:
            raise CalibrationProfileError(f"profile is missing {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise CalibrationProfileError(f"invalid profile field: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CalibrationApplicability:
    status: CalibrationApplicationStatus
    reason: str
    profile_id: str | None = None
    profile_version: int | None = None

    @property
    def applicable(self) -> bool:
        return self.status in (
            CalibrationApplicationStatus.CALIBRATED,
            CalibrationApplicationStatus.INTERPOLATED,
            CalibrationApplicationStatus.EXTRAPOLATED,
        )

    @property
    def contract_status(self) -> CalibrationStatus:
        return {
            CalibrationApplicationStatus.CALIBRATED: CalibrationStatus.APPLIED,
            CalibrationApplicationStatus.INTERPOLATED: CalibrationStatus.INTERPOLATED,
            CalibrationApplicationStatus.EXTRAPOLATED: CalibrationStatus.EXTRAPOLATED,
            CalibrationApplicationStatus.UNCALIBRATED: CalibrationStatus.UNCALIBRATED,
            CalibrationApplicationStatus.INVALID_FOR_SETTINGS: CalibrationStatus.INVALID,
        }[self.status]


def check_applicability(
    profile: CalibrationProfile,
    settings: CalibrationSignature | None,
    *,
    frequency_hz: float | None = None,
    allow_extrapolation: bool = False,
) -> CalibrationApplicability:
    """Validate identity/settings before changing dBFS into dBm."""

    def invalid(reason: str) -> CalibrationApplicability:
        return CalibrationApplicability(
            CalibrationApplicationStatus.INVALID_FOR_SETTINGS,
            reason,
            profile.profile_id,
            profile.profile_version,
        )

    if settings is None or not settings.device_serial:
        return invalid("device serial/settings are missing")
    expected = profile.signature
    text_fields = (
        ("device serial", expected.device_serial, settings.device_serial),
        ("backend", expected.backend, settings.backend),
        ("RF port/path", expected.rf_port_path, settings.rf_port_path),
        ("gain mode", expected.gain_mode, settings.gain_mode),
        ("window normalization version", expected.window_normalization_version, settings.window_normalization_version),
        ("FFT unit convention", expected.fft_unit_convention, settings.fft_unit_convention),
        ("frontend chain", expected.frontend_chain, settings.frontend_chain),
        ("reference plane", expected.reference_plane, settings.reference_plane),
    )
    for label, expected_value, actual_value in text_fields:
        if expected_value != actual_value:
            return invalid(f"incompatible {label}")
    if not _same_float(expected.sample_rate_hz, settings.sample_rate_hz):
        return invalid("incompatible sample rate")
    if not _same_float(expected.analog_bandwidth_hz, settings.analog_bandwidth_hz):
        return invalid("incompatible analog bandwidth")
    if not _same_float(expected.manual_gain_db, settings.manual_gain_db):
        return invalid("incompatible gain")
    if expected.temperature_range_c is not None and expected.temperature_range_c != settings.temperature_range_c:
        return invalid("temperature applicability is unknown")
    if frequency_hz is None:
        return CalibrationApplicability(
            CalibrationApplicationStatus.CALIBRATED,
            "settings match calibration profile",
            profile.profile_id,
            profile.profile_version,
        )
    sample = profile.evaluate(frequency_hz, allow_extrapolation=allow_extrapolation)
    reason = "frequency is covered" if sample.status is not CalibrationApplicationStatus.INVALID_FOR_SETTINGS else "frequency is outside the applicable calibration range"
    return CalibrationApplicability(sample.status, reason, profile.profile_id, profile.profile_version)


@dataclass(frozen=True, slots=True)
class PreparedCalibration:
    profile_id: str
    profile_version: int
    status: CalibrationApplicationStatus
    correction_db: np.ndarray
    uncertainty_db: np.ndarray

    def __post_init__(self) -> None:
        correction = np.asarray(self.correction_db, dtype=np.float64).reshape(-1)
        uncertainty = np.asarray(self.uncertainty_db, dtype=np.float64).reshape(-1)
        if correction.size != uncertainty.size:
            raise CalibrationProfileError("correction and uncertainty grids must have equal length")
        correction.setflags(write=False)
        uncertainty.setflags(write=False)
        object.__setattr__(self, "correction_db", correction)
        object.__setattr__(self, "uncertainty_db", uncertainty)

    def apply(
        self,
        raw_values_dbfs: Sequence[float] | np.ndarray,
        *,
        output: np.ndarray | None = None,
        raw_uncertainty_db: Sequence[float] | np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        if self.status in (CalibrationApplicationStatus.INVALID_FOR_SETTINGS, CalibrationApplicationStatus.UNCALIBRATED):
            raise CalibrationProfileError("cannot apply an unavailable calibration")
        raw = np.asarray(raw_values_dbfs, dtype=np.float64).reshape(-1)
        if raw.size != self.correction_db.size:
            raise CalibrationProfileError("raw values and prepared correction grid have different lengths")
        target = np.empty_like(raw) if output is None else np.asarray(output)
        if target.shape != raw.shape or not np.issubdtype(target.dtype, np.floating):
            raise CalibrationProfileError("output must be a same-size floating-point array")
        np.add(raw, self.correction_db, out=target, casting="unsafe")
        if raw_uncertainty_db is None:
            total_uncertainty = np.array(self.uncertainty_db, copy=True)
        else:
            raw_uncertainty = np.asarray(raw_uncertainty_db, dtype=np.float64).reshape(-1)
            if raw_uncertainty.size != raw.size or np.any(raw_uncertainty < 0.0) or not np.all(np.isfinite(raw_uncertainty)):
                raise CalibrationProfileError("raw uncertainty must be finite, non-negative and grid-sized")
            total_uncertainty = np.hypot(raw_uncertainty, self.uncertainty_db)
        total_uncertainty.setflags(write=False)
        return target, total_uncertainty


@dataclass(frozen=True, slots=True)
class CalibratedSpectrum:
    values_db: np.ndarray
    unit: SpectrumUnit
    status: CalibrationApplicationStatus
    calibration_profile_id: str | None
    correction_db: np.ndarray
    uncertainty_db: np.ndarray

    def __post_init__(self) -> None:
        arrays = tuple(
            np.asarray(value, dtype=np.float64).reshape(-1)
            for value in (self.values_db, self.correction_db, self.uncertainty_db)
        )
        if len({array.size for array in arrays}) != 1:
            raise CalibrationProfileError("spectrum result arrays must have equal length")
        for name, array in zip(("values_db", "correction_db", "uncertainty_db"), arrays, strict=True):
            array.setflags(write=False)
            object.__setattr__(self, name, array)

    @property
    def contract_status(self) -> CalibrationStatus:
        return CalibrationApplicability(self.status, "").contract_status


def _raw_result(
    values: Sequence[float] | np.ndarray,
    raw_unit: SpectrumUnit,
    status: CalibrationApplicationStatus,
    profile_id: str | None,
) -> CalibratedSpectrum:
    if raw_unit not in (SpectrumUnit.DBFS_BIN, SpectrumUnit.DBFS_HZ):
        raise CalibrationProfileError("raw calibration input must use a dBFS unit")
    raw = np.asarray(values, dtype=np.float64).reshape(-1)
    return CalibratedSpectrum(
        raw,
        raw_unit,
        status,
        profile_id,
        np.zeros(raw.size, dtype=np.float64),
        np.full(raw.size, np.nan, dtype=np.float64),
    )


def _calibrated_unit(raw_unit: SpectrumUnit) -> SpectrumUnit:
    if raw_unit is SpectrumUnit.DBFS_BIN:
        return SpectrumUnit.DBM_BIN
    if raw_unit is SpectrumUnit.DBFS_HZ:
        return SpectrumUnit.DBM_HZ
    raise CalibrationProfileError("raw calibration input must use a dBFS unit")


class CalibrationApplier:
    """Bounded cache of correction arrays; no file I/O occurs in apply."""

    def __init__(self, *, max_entries: int = 16) -> None:
        if isinstance(max_entries, bool) or max_entries <= 0:
            raise CalibrationProfileError("max_entries must be positive")
        self._max_entries = int(max_entries)
        self._cache: dict[str, PreparedCalibration] = {}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()

    def invalidate_profile(self, profile_id: str, profile_version: int | None = None) -> None:
        keys = [
            key for key, value in self._cache.items()
            if value.profile_id == profile_id and (profile_version is None or value.profile_version == profile_version)
        ]
        for key in keys:
            self._cache.pop(key, None)

    @staticmethod
    def _key(profile: CalibrationProfile, settings: CalibrationSignature, frequencies: np.ndarray, allow_extrapolation: bool) -> str:
        grid = np.ascontiguousarray(frequencies, dtype=np.float64)
        settings_json = json.dumps(settings.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(grid.tobytes() + settings_json.encode("utf-8")).hexdigest()
        return f"{profile.fingerprint}:{digest}:{int(allow_extrapolation)}"

    def prepare(
        self,
        profile: CalibrationProfile,
        settings: CalibrationSignature | None,
        frequencies_hz: Sequence[float] | np.ndarray,
        *,
        allow_extrapolation: bool = False,
    ) -> PreparedCalibration | None:
        if settings is None:
            return None
        frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
        if frequencies.size == 0:
            raise CalibrationProfileError("frequency grid must not be empty")
        if not check_applicability(profile, settings, allow_extrapolation=allow_extrapolation).applicable:
            return None
        key = self._key(profile, settings, frequencies, allow_extrapolation)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        prepared = profile.prepare_grid(frequencies, allow_extrapolation=allow_extrapolation)
        if prepared.status is CalibrationApplicationStatus.INVALID_FOR_SETTINGS:
            return None
        if len(self._cache) >= self._max_entries:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = prepared
        return prepared


def apply_calibration(
    frequencies_hz: Sequence[float] | np.ndarray,
    values_dbfs: Sequence[float] | np.ndarray,
    *,
    profile: CalibrationProfile | None,
    settings: CalibrationSignature | None,
    raw_unit: SpectrumUnit = SpectrumUnit.DBFS_BIN,
    allow_extrapolation: bool = False,
    raw_uncertainty_db: Sequence[float] | np.ndarray | None = None,
    applier: CalibrationApplier | None = None,
) -> CalibratedSpectrum:
    """Apply correction after normalized dBFS is computed.

    Missing/incompatible profiles return the original dBFS values and never
    relabel them as dBm.
    """

    frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
    values = np.asarray(values_dbfs, dtype=np.float64).reshape(-1)
    if frequencies.size == 0 or values.size != frequencies.size or not np.all(np.isfinite(frequencies)):
        raise CalibrationProfileError("frequencies and values must be finite, non-empty and equal-sized")
    if profile is None:
        return _raw_result(values, raw_unit, CalibrationApplicationStatus.UNCALIBRATED, None)
    prepared = (applier or CalibrationApplier()).prepare(
        profile, settings, frequencies, allow_extrapolation=allow_extrapolation
    )
    if prepared is None:
        return _raw_result(values, raw_unit, CalibrationApplicationStatus.INVALID_FOR_SETTINGS, profile.profile_id)
    corrected, uncertainty = prepared.apply(values, raw_uncertainty_db=raw_uncertainty_db)
    return CalibratedSpectrum(
        corrected,
        _calibrated_unit(raw_unit),
        prepared.status,
        profile.profile_id,
        prepared.correction_db,
        uncertainty,
    )


def profile_from_csv(
    path: str | os.PathLike[str],
    *,
    profile_id: str,
    profile_version: int,
    signature: CalibrationSignature,
    reference_equipment: str = "",
    notes: str = "",
    created_at: str = "",
    valid_start_hz: float | None = None,
    valid_stop_hz: float | None = None,
) -> CalibrationProfile:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or any(name not in reader.fieldnames for name in _CSV_COLUMNS):
            raise CalibrationProfileError("CSV is missing one of the five required P09 columns")
        points: list[CalibrationPoint] = []
        for row_number, row in enumerate(reader, 2):
            try:
                values: dict[str, float] = {}
                for name in _CSV_COLUMNS:
                    raw = row.get(name)
                    if raw is None or not raw.strip():
                        raise CalibrationProfileError(f"CSV field {name!r} is empty")
                    values[name] = _finite(raw, name)
                points.append(CalibrationPoint(**values))
            except CalibrationProfileError as exc:
                raise CalibrationProfileError(f"invalid calibration CSV row {row_number}: {exc}") from exc
    if not points:
        raise CalibrationProfileError("calibration CSV contains no points")
    return CalibrationProfile(
        profile_id=profile_id,
        profile_version=profile_version,
        signature=signature,
        reference_plane=signature.reference_plane,
        points=tuple(points),
        reference_equipment=reference_equipment,
        notes=notes,
        created_at=created_at,
        valid_start_hz=valid_start_hz,
        valid_stop_hz=valid_stop_hz,
    )


class CalibrationProfileStore:
    """Atomic JSON store keyed by immutable profile id and version."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    @staticmethod
    def _filename(profile_id: str, profile_version: int) -> str:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise CalibrationProfileError("profile_id contains unsupported path characters")
        if isinstance(profile_version, bool) or not isinstance(profile_version, int) or profile_version <= 0:
            raise CalibrationProfileError("profile_version must be a positive integer")
        return f"{profile_id}.v{profile_version}.json"

    def path_for(self, profile: CalibrationProfile) -> Path:
        return self.root / self._filename(profile.profile_id, profile.profile_version)

    def save(self, profile: CalibrationProfile) -> Path:
        target = self.path_for(profile)
        self.root.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = self.load(profile.profile_id, profile.profile_version)
            if existing.fingerprint != profile.fingerprint:
                raise CalibrationProfileError("finalized calibration profile version is immutable")
            return target
        payload = json.dumps(profile.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        temporary: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=self.root)
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
            return target
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def load(self, profile_id: str, profile_version: int | None = None) -> CalibrationProfile:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise CalibrationProfileError('profile_id contains unsupported path characters')
        if profile_version is not None:
            path = self.root / self._filename(profile_id, profile_version)
        else:
            candidates: list[tuple[int, Path]] = []
            for candidate in self.root.glob(f"{profile_id}.v*.json"):
                match = re.fullmatch(re.escape(profile_id) + r"\.v([0-9]+)\.json", candidate.name)
                if match:
                    candidates.append((int(match.group(1)), candidate))
            if not candidates:
                raise CalibrationProfileError("calibration profile was not found")
            path = max(candidates, key=lambda item: item[0])[1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CalibrationProfileError("calibration profile was not found") from exc
        except json.JSONDecodeError as exc:
            raise CalibrationProfileError(f"corrupted calibration profile JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise CalibrationProfileError("calibration profile root must be an object")
        return CalibrationProfile.from_dict(payload)

    def list_profiles(self) -> tuple[CalibrationProfile, ...]:
        profiles: list[CalibrationProfile] = []
        for path in sorted(self.root.glob("*.v*.json")):
            match = re.fullmatch(r"(.+)\.v([0-9]+)\.json", path.name)
            if match:
                profiles.append(self.load(match.group(1), int(match.group(2))))
        return tuple(sorted(profiles, key=lambda item: (item.profile_id, item.profile_version)))


def validate_profile_file(path: str | os.PathLike[str]) -> CalibrationProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise CalibrationProfileError("calibration profile root must be an object")
    return CalibrationProfile.from_dict(payload)


def calibration_contract_status(status: CalibrationApplicationStatus) -> CalibrationStatus:
    return CalibrationApplicability(status, "").contract_status


__all__ = [
    "CALIBRATION_SCHEMA_NAME",
    "CALIBRATION_SCHEMA_VERSION",
    "CalibrationApplicationStatus",
    "CalibrationApplicability",
    "CalibrationApplier",
    "CalibrationPoint",
    "CalibrationProfile",
    "CalibrationProfileError",
    "CalibrationProfileStore",
    "CalibrationResultStatus",
    "CalibrationSample",
    "CalibrationSettings",
    "CalibrationSignature",
    "CalibratedSpectrum",
    "PreparedCalibration",
    "apply_calibration",
    "calibration_contract_status",
    "check_applicability",
    "profile_from_csv",
    "validate_profile_file",
]
