"""Standalone immutable calibration and unit-safety contracts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Sequence, cast

import numpy as np


class CalibrationProfileError(ValueError):
    pass


class CalibrationStatus(StrEnum):
    CALIBRATED = "calibrated"
    INTERPOLATED = "interpolated"
    EXTRAPOLATED = "extrapolated"
    UNCALIBRATED = "uncalibrated"
    INVALID = "invalid_for_settings"


class MeasurementQuality(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


def _finite(value: object, name: str) -> float:
    try:
        result = float(cast(object, value))
    except (TypeError, ValueError) as error:
        raise CalibrationProfileError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise CalibrationProfileError(f"{name} must be finite")
    return result


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationProfileError(f"{name} must not be empty")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CalibrationSignature:
    device_serial: str = "unknown"
    backend: str = "cpu"
    rf_port_path: str = "rx"
    sample_rate_hz: float = 1e6
    analog_bandwidth_hz: float = 800e3
    gain_mode: str = "manual"
    manual_gain_db: float = 0.0
    window_normalization_version: str = "standalone-v1"
    fft_unit_convention: str = "dBFS/bin"
    frontend_chain: str = "unknown"
    reference_plane: str = "rf_input"

    def __post_init__(self) -> None:
        for name in ("device_serial", "backend", "rf_port_path", "gain_mode", "window_normalization_version", "fft_unit_convention", "frontend_chain", "reference_plane"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("sample_rate_hz", "analog_bandwidth_hz", "manual_gain_db"):
            value = _finite(getattr(self, name), name)
            if name != "manual_gain_db" and value <= 0:
                raise CalibrationProfileError(f"{name} must be positive")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CalibrationSignature":
        defaults = cls()
        return cls(**{name: payload.get(name, getattr(defaults, name)) for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    frequency_hz: float
    correction_db: float
    uncertainty_db: float
    reference_dbm: float = 0.0
    measured_dbfs: float = 0.0

    def __post_init__(self) -> None:
        frequency = _finite(self.frequency_hz, "frequency_hz")
        if frequency <= 0:
            raise CalibrationProfileError("frequency_hz must be positive")
        uncertainty = _finite(self.uncertainty_db, "uncertainty_db")
        if uncertainty < 0:
            raise CalibrationProfileError("uncertainty_db must not be negative")
        object.__setattr__(self, "frequency_hz", frequency)
        object.__setattr__(self, "correction_db", _finite(self.correction_db, "correction_db"))
        object.__setattr__(self, "uncertainty_db", uncertainty)
        object.__setattr__(self, "reference_dbm", _finite(self.reference_dbm, "reference_dbm"))
        object.__setattr__(self, "measured_dbfs", _finite(self.measured_dbfs, "measured_dbfs"))

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    correction_db: float
    uncertainty_db: float
    status: CalibrationStatus


@dataclass(frozen=True, slots=True)
class ApplicabilityRow:
    label: str
    expected: str
    actual: str
    matches: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CalibrationApplicability:
    status: CalibrationStatus
    reason: str
    rows: tuple[ApplicabilityRow, ...] = ()
    profile_id: str | None = None
    profile_version: int | None = None

    @property
    def applicable(self) -> bool:
        return self.status in (CalibrationStatus.CALIBRATED, CalibrationStatus.INTERPOLATED, CalibrationStatus.EXTRAPOLATED)


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    profile_id: str
    profile_version: int
    signature: CalibrationSignature
    points: tuple[CalibrationPoint, ...]
    reference_plane: str = "rf_input"
    created_at: str = ""
    reference_equipment: str = ""
    notes: str = ""
    finalized: bool = True

    def __post_init__(self) -> None:
        profile_id = _text(self.profile_id, "profile_id")
        if any(char in profile_id for char in "\\/:"):
            raise CalibrationProfileError("profile_id contains path characters")
        if not isinstance(self.profile_version, int) or self.profile_version <= 0:
            raise CalibrationProfileError("profile_version must be positive")
        if not self.finalized:
            raise CalibrationProfileError("only finalized immutable profiles are accepted")
        points = tuple(self.points)
        if len(points) < 2 or any(left.frequency_hz >= right.frequency_hz for left, right in zip(points, points[1:])):
            raise CalibrationProfileError("points must contain at least two strictly increasing frequencies")
        if self.reference_plane != self.signature.reference_plane:
            raise CalibrationProfileError("reference plane must match signature")
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "created_at", self.created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat())

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "sdr-calibration-profile",
            "schema_version": 1,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "finalized": self.finalized,
            "signature": self.signature.to_dict(),
            "reference_plane": self.reference_plane,
            "created_at": self.created_at,
            "reference_equipment": self.reference_equipment,
            "notes": self.notes,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CalibrationProfile":
        if payload.get("schema") != "sdr-calibration-profile" or payload.get("schema_version") != 1:
            raise CalibrationProfileError("unknown calibration profile schema")
        raw_points = payload.get("points")
        signature = payload.get("signature")
        if not isinstance(raw_points, list) or not isinstance(signature, Mapping):
            raise CalibrationProfileError("profile requires signature and points")
        points = tuple(CalibrationPoint(**{name: float(item.get(name, 0.0)) for name in CalibrationPoint.__dataclass_fields__}) for item in raw_points if isinstance(item, Mapping))
        return cls(
            profile_id=str(payload.get("profile_id", "")),
            profile_version=int(payload.get("profile_version", 0)),
            signature=CalibrationSignature.from_dict(signature),
            points=points,
            reference_plane=str(payload.get("reference_plane", "rf_input")),
            created_at=str(payload.get("created_at", "")),
            reference_equipment=str(payload.get("reference_equipment", "")),
            notes=str(payload.get("notes", "")),
            finalized=bool(payload.get("finalized", True)),
        )

    def evaluate(self, frequency_hz: float, *, allow_extrapolation: bool = False) -> CalibrationSample:
        frequency = _finite(frequency_hz, "frequency_hz")
        grid = np.asarray([point.frequency_hz for point in self.points], dtype=np.float64)
        corrections = np.asarray([point.correction_db for point in self.points], dtype=np.float64)
        uncertainties = np.asarray([point.uncertainty_db for point in self.points], dtype=np.float64)
        if frequency < grid[0] or frequency > grid[-1]:
            if not allow_extrapolation:
                return CalibrationSample(0.0, float("nan"), CalibrationStatus.INVALID)
            left, right = (0, 1) if frequency < grid[0] else (-2, -1)
            status = CalibrationStatus.EXTRAPOLATED
        else:
            right = int(np.searchsorted(grid, frequency, side="left"))
            if right < len(grid) and grid[right] == frequency:
                return CalibrationSample(float(corrections[right]), float(uncertainties[right]), CalibrationStatus.CALIBRATED)
            left, right = right - 1, right
            status = CalibrationStatus.INTERPOLATED
        fraction = (frequency - grid[left]) / (grid[right] - grid[left])
        return CalibrationSample(float(corrections[left] + fraction * (corrections[right] - corrections[left])), float(uncertainties[left] + fraction * (uncertainties[right] - uncertainties[left])), status)


def check_applicability(profile: CalibrationProfile, settings: CalibrationSignature | None) -> CalibrationApplicability:
    fields = (
        ("Device serial", "device_serial"), ("Backend", "backend"), ("RF path", "rf_port_path"),
        ("Sample rate", "sample_rate_hz"), ("Analog bandwidth", "analog_bandwidth_hz"), ("Gain mode", "gain_mode"),
        ("Manual gain", "manual_gain_db"), ("Window normalization", "window_normalization_version"),
        ("FFT units", "fft_unit_convention"), ("Frontend chain", "frontend_chain"), ("Reference plane", "reference_plane"),
    )
    rows = []
    for label, name in fields:
        expected = getattr(profile.signature, name)
        actual = getattr(settings, name) if settings is not None else None
        matches = settings is not None and (math.isclose(float(expected), float(actual), abs_tol=1e-6, rel_tol=1e-9) if isinstance(expected, (int, float)) else expected == actual)
        rows.append(ApplicabilityRow(label, str(expected), "—" if actual is None else str(actual), matches, "" if matches else "Несовпадение"))
    mismatches = tuple(row for row in rows if not row.matches)
    status = CalibrationStatus.CALIBRATED if not mismatches else CalibrationStatus.INVALID
    reason = "Профиль применим" if not mismatches else "; ".join(row.label for row in mismatches) + " не совпадает"
    return CalibrationApplicability(status, reason, tuple(rows), profile.profile_id, profile.profile_version)


def apply_calibration(values: Sequence[float] | np.ndarray, frequencies_hz: Sequence[float] | np.ndarray, profile: CalibrationProfile | None, settings: CalibrationSignature | None, *, allow_extrapolation: bool = False) -> "CalibratedArray":
    raw = np.asarray(values, dtype=np.float64).reshape(-1)
    frequencies = np.asarray(frequencies_hz, dtype=np.float64).reshape(-1)
    if raw.size != frequencies.size or raw.size == 0:
        raise CalibrationProfileError("values and frequencies must have equal non-zero length")
    if profile is None:
        return CalibratedArray(raw.copy(), np.full(raw.size, np.nan), "dBFS/bin", CalibrationStatus.UNCALIBRATED, None)
    applicability = check_applicability(profile, settings)
    if not applicability.applicable:
        return CalibratedArray(raw.copy(), np.full(raw.size, np.nan), "dBFS/bin", CalibrationStatus.INVALID, profile.profile_id)
    samples = tuple(profile.evaluate(float(item), allow_extrapolation=allow_extrapolation) for item in frequencies)
    if any(item.status is CalibrationStatus.INVALID for item in samples):
        return CalibratedArray(raw.copy(), np.full(raw.size, np.nan), "dBFS/bin", CalibrationStatus.INVALID, profile.profile_id)
    status = CalibrationStatus.EXTRAPOLATED if any(item.status is CalibrationStatus.EXTRAPOLATED for item in samples) else CalibrationStatus.INTERPOLATED if any(item.status is CalibrationStatus.INTERPOLATED for item in samples) else CalibrationStatus.CALIBRATED
    return CalibratedArray(raw + np.asarray([item.correction_db for item in samples]), np.asarray([item.uncertainty_db for item in samples]), "dBm/bin", status, profile.profile_id)


@dataclass(frozen=True, slots=True)
class CalibratedArray:
    values: np.ndarray
    uncertainty_db: np.ndarray
    unit: str
    status: CalibrationStatus
    profile_id: str | None


@dataclass(frozen=True, slots=True)
class CalibrationImportPreview:
    valid: bool
    source_name: str
    profile_id: str
    profile_version: int
    points: tuple[CalibrationPoint, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def point_rows(self) -> tuple[tuple[float, float, float], ...]:
        return tuple((point.frequency_hz, point.correction_db, point.uncertainty_db) for point in self.points)


def preview_calibration_csv(path: Path, profile_id: str, profile_version: int) -> CalibrationImportPreview:
    errors: list[str] = []
    points: list[CalibrationPoint] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            required = {"frequency_hz", "correction_db", "uncertainty_db"}
            if not required.issubset(rows.fieldnames or ()):
                errors.append("CSV must contain frequency_hz, correction_db and uncertainty_db")
            else:
                for row_number, row in enumerate(rows, 2):
                    try:
                        points.append(CalibrationPoint(float(row["frequency_hz"]), float(row["correction_db"]), float(row["uncertainty_db"]), float(row.get("reference_dbm") or 0.0), float(row.get("measured_dbfs") or 0.0)))
                    except (TypeError, ValueError, CalibrationProfileError) as error:
                        errors.append(f"row {row_number}: {error}")
    except (OSError, UnicodeError) as error:
        errors.append(str(error))
    if not errors:
        try:
            tuple(sorted(points, key=lambda point: point.frequency_hz))
            if len(points) < 2 or any(left.frequency_hz >= right.frequency_hz for left, right in zip(points, points[1:])):
                raise CalibrationProfileError("points must be strictly increasing and contain at least two rows")
        except CalibrationProfileError as error:
            errors.append(str(error))
    return CalibrationImportPreview(not errors, path.name, profile_id, profile_version, tuple(points) if not errors else (), tuple(errors))


@dataclass(frozen=True, slots=True)
class MeasurementValue:
    measurement_id: str
    title: str
    value: float | None
    unit: str
    quality: MeasurementQuality
    uncertainty_db: float | None
    frame_sequence: int | None
    config_generation: int | None
    source_id: str
    calibration_status: CalibrationStatus
    warning: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.measurement_id, str) or not self.measurement_id.strip():
            raise ValueError("measurement id must not be empty")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("measurement title must not be empty")
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise ValueError("measurement unit must not be empty")
        if self.value is not None and not math.isfinite(float(self.value)):
            raise ValueError("measurement value must be finite")
        if self.uncertainty_db is not None and (not math.isfinite(float(self.uncertainty_db)) or self.uncertainty_db < 0):
            raise ValueError("measurement uncertainty must be finite and non-negative")
        if self.unit.lower().startswith("dbm") and self.calibration_status in (CalibrationStatus.UNCALIBRATED, CalibrationStatus.INVALID):
            raise ValueError("absolute dBm requires a valid calibration profile")


__all__ = ["ApplicabilityRow", "CalibratedArray", "CalibrationApplicability", "CalibrationImportPreview", "CalibrationPoint", "CalibrationProfile", "CalibrationProfileError", "CalibrationSignature", "CalibrationStatus", "MeasurementQuality", "MeasurementValue", "apply_calibration", "check_applicability", "preview_calibration_csv"]
