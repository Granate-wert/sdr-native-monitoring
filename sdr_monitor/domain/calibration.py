"""Standalone immutable calibration and unit-safety contracts."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Sequence, SupportsFloat, SupportsIndex, cast

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


_REQUIRED_PROVENANCE_FIELDS = frozenset(
    {"device_family", "adapter_id", "device_identity_key", "firmware_fingerprint"}
)
_PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_calibration_profile_id(value: object) -> str:
    if not isinstance(value, str) or not _PROFILE_ID_PATTERN.fullmatch(value):
        raise CalibrationProfileError("profile_id must match the calibration schema identifier pattern")
    return value


def _opaque_or_unknown(value: object, name: str, *, digest: bool = False) -> str:
    normalized = _text(value, name)
    if normalized == "unknown":
        return normalized
    if digest:
        payload = normalized[7:] if normalized.startswith("sha256:") else ""
        if len(payload) != 64 or any(character not in "0123456789abcdef" for character in payload):
            raise CalibrationProfileError(f"{name} must be unknown or an opaque sha256 digest")
        return normalized
    if len(normalized) > 128 or not normalized[0].isalnum() or any(
        not (character.isascii() and (character.isalnum() or character in "._-"))
        for character in normalized
    ):
        raise CalibrationProfileError(f"{name} must be unknown or an opaque identifier")
    return normalized


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise CalibrationProfileError(f"{name} must be numeric")
    try:
        result = float(cast(str | bytes | bytearray | SupportsFloat | SupportsIndex, value))
    except (TypeError, ValueError) as error:
        raise CalibrationProfileError(f"{name} must be numeric") from error
    if not math.isfinite(result):
        raise CalibrationProfileError(f"{name} must be finite")
    return result


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationProfileError(f"{name} must not be empty")
    return value.strip()


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise CalibrationProfileError(f"{name} must be an integer")
    try:
        numeric = _finite(value, name)
    except (TypeError, ValueError) as error:
        raise CalibrationProfileError(f"{name} must be an integer") from error
    if not numeric.is_integer():
        raise CalibrationProfileError(f"{name} must be an integer")
    return int(numeric)


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
    device_family: str = "unknown"
    adapter_id: str = "unknown"
    device_identity_key: str = "unknown"
    firmware_fingerprint: str = "unknown"
    temperature_range_c: tuple[float, float] | None = None

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
        object.__setattr__(self, "device_family", _opaque_or_unknown(self.device_family, "device_family"))
        object.__setattr__(self, "adapter_id", _opaque_or_unknown(self.adapter_id, "adapter_id"))
        object.__setattr__(
            self,
            "device_identity_key",
            _opaque_or_unknown(self.device_identity_key, "device_identity_key", digest=True),
        )
        object.__setattr__(
            self,
            "firmware_fingerprint",
            _opaque_or_unknown(self.firmware_fingerprint, "firmware_fingerprint", digest=True),
        )
        for name in ("sample_rate_hz", "analog_bandwidth_hz", "manual_gain_db"):
            value = _finite(getattr(self, name), name)
            if name != "manual_gain_db" and value <= 0:
                raise CalibrationProfileError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.temperature_range_c is not None:
            if not isinstance(self.temperature_range_c, (tuple, list)) or len(self.temperature_range_c) != 2:
                raise CalibrationProfileError("temperature_range_c must contain two values")
            low = _finite(self.temperature_range_c[0], "temperature_range_c[0]")
            high = _finite(self.temperature_range_c[1], "temperature_range_c[1]")
            if high < low:
                raise CalibrationProfileError("temperature_range_c must be ordered")
            object.__setattr__(self, "temperature_range_c", (low, high))

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CalibrationSignature":
        defaults = cls()
        extra = set(payload) - set(cls.__dataclass_fields__)
        if extra:
            raise CalibrationProfileError("signature contains unsupported fields: " + ", ".join(sorted(extra)))
        raw_temperature = payload.get("temperature_range_c", defaults.temperature_range_c)
        temperature: tuple[float, float] | None
        if raw_temperature is None:
            temperature = None
        elif isinstance(raw_temperature, (tuple, list)) and len(raw_temperature) == 2:
            temperature = (
                _finite(raw_temperature[0], "temperature_range_c[0]"),
                _finite(raw_temperature[1], "temperature_range_c[1]"),
            )
        else:
            raise CalibrationProfileError("temperature_range_c must contain two values or be null")
        return cls(
            device_serial=_text(payload.get("device_serial", defaults.device_serial), "device_serial"),
            backend=_text(payload.get("backend", defaults.backend), "backend"),
            rf_port_path=_text(payload.get("rf_port_path", defaults.rf_port_path), "rf_port_path"),
            sample_rate_hz=_finite(payload.get("sample_rate_hz", defaults.sample_rate_hz), "sample_rate_hz"),
            analog_bandwidth_hz=_finite(payload.get("analog_bandwidth_hz", defaults.analog_bandwidth_hz), "analog_bandwidth_hz"),
            gain_mode=_text(payload.get("gain_mode", defaults.gain_mode), "gain_mode"),
            manual_gain_db=_finite(payload.get("manual_gain_db", defaults.manual_gain_db), "manual_gain_db"),
            window_normalization_version=_text(payload.get("window_normalization_version", defaults.window_normalization_version), "window_normalization_version"),
            fft_unit_convention=_text(payload.get("fft_unit_convention", defaults.fft_unit_convention), "fft_unit_convention"),
            frontend_chain=_text(payload.get("frontend_chain", defaults.frontend_chain), "frontend_chain"),
            reference_plane=_text(payload.get("reference_plane", defaults.reference_plane), "reference_plane"),
            device_family=_text(payload.get("device_family", defaults.device_family), "device_family"),
            adapter_id=_text(payload.get("adapter_id", defaults.adapter_id), "adapter_id"),
            device_identity_key=_text(
                payload.get("device_identity_key", defaults.device_identity_key), "device_identity_key"
            ),
            firmware_fingerprint=_text(
                payload.get("firmware_fingerprint", defaults.firmware_fingerprint), "firmware_fingerprint"
            ),
            temperature_range_c=temperature,
        )


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
    interpolation_method: str = "linear"
    valid_start_hz: float | None = None
    valid_stop_hz: float | None = None

    def __post_init__(self) -> None:
        profile_id = validate_calibration_profile_id(self.profile_id)
        if isinstance(self.profile_version, bool) or not isinstance(self.profile_version, int) or self.profile_version <= 0:
            raise CalibrationProfileError("profile_version must be positive")
        if not isinstance(self.finalized, bool) or not self.finalized:
            raise CalibrationProfileError("only finalized immutable profiles are accepted")
        if not isinstance(self.signature, CalibrationSignature):
            raise CalibrationProfileError("signature must be CalibrationSignature")
        points = tuple(self.points)
        if len(points) < 2 or any(not isinstance(point, CalibrationPoint) for point in points):
            raise CalibrationProfileError("points must contain at least two calibration points")
        if any(left.frequency_hz >= right.frequency_hz for left, right in zip(points, points[1:])):
            raise CalibrationProfileError("points must contain at least two strictly increasing frequencies")
        if self.reference_plane != self.signature.reference_plane:
            raise CalibrationProfileError("reference plane must match signature")
        if self.interpolation_method != "linear":
            raise CalibrationProfileError("only linear calibration interpolation is supported")
        valid_start = points[0].frequency_hz if self.valid_start_hz is None else _finite(
            self.valid_start_hz, "valid_start_hz"
        )
        valid_stop = points[-1].frequency_hz if self.valid_stop_hz is None else _finite(
            self.valid_stop_hz, "valid_stop_hz"
        )
        if valid_start != points[0].frequency_hz or valid_stop != points[-1].frequency_hz:
            raise CalibrationProfileError("valid range must exactly match the calibration point grid")
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "valid_start_hz", valid_start)
        object.__setattr__(self, "valid_stop_hz", valid_stop)
        created_at = self.created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        object.__setattr__(self, "created_at", _text(created_at, "created_at"))
        if not isinstance(self.reference_equipment, str) or not isinstance(self.notes, str):
            raise CalibrationProfileError("reference_equipment and notes must be text")

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
        allowed = {
            "schema", "schema_version", "profile_id", "profile_version", "finalized", "signature",
            "reference_plane", "interpolation_method", "valid_start_hz", "valid_stop_hz", "created_at",
            "reference_equipment", "notes", "points",
        }
        extra = set(payload) - allowed
        if extra:
            raise CalibrationProfileError("profile contains unsupported fields: " + ", ".join(sorted(extra)))
        schema_version = payload.get("schema_version")
        if (
            payload.get("schema") != "sdr-calibration-profile"
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            raise CalibrationProfileError("unknown calibration profile schema")
        raw_points = payload.get("points")
        signature = payload.get("signature")
        if not isinstance(raw_points, list) or not isinstance(signature, Mapping):
            raise CalibrationProfileError("profile requires signature and points")
        if any(not isinstance(item, Mapping) for item in raw_points):
            raise CalibrationProfileError("profile points must be objects")
        point_fields = set(CalibrationPoint.__dataclass_fields__)
        if any(set(item) - point_fields for item in raw_points if isinstance(item, Mapping)):
            raise CalibrationProfileError("calibration point contains unsupported fields")
        points = tuple(
            CalibrationPoint(
                frequency_hz=_finite(item.get("frequency_hz", 0.0), "frequency_hz"),
                correction_db=_finite(item.get("correction_db", 0.0), "correction_db"),
                uncertainty_db=_finite(item.get("uncertainty_db", 0.0), "uncertainty_db"),
                reference_dbm=_finite(item.get("reference_dbm", 0.0), "reference_dbm"),
                measured_dbfs=_finite(item.get("measured_dbfs", 0.0), "measured_dbfs"),
            )
            for item in raw_points
            if isinstance(item, Mapping)
        )
        return cls(
            profile_id=validate_calibration_profile_id(payload.get("profile_id")),
            profile_version=_integer(payload.get("profile_version", 0), "profile_version"),
            signature=CalibrationSignature.from_dict(signature),
            points=points,
            reference_plane=_text(payload.get("reference_plane", "rf_input"), "reference_plane"),
            created_at=_text(payload.get("created_at", ""), "created_at"),
            reference_equipment=payload.get("reference_equipment", ""),  # type: ignore[arg-type]
            notes=payload.get("notes", ""),  # type: ignore[arg-type]
            finalized=payload.get("finalized", True),  # type: ignore[arg-type]
            interpolation_method=_text(payload.get("interpolation_method", "linear"), "interpolation_method"),
            valid_start_hz=(
                None
                if payload.get("valid_start_hz") is None
                else _finite(payload.get("valid_start_hz"), "valid_start_hz")
            ),
            valid_stop_hz=(
                None
                if payload.get("valid_stop_hz") is None
                else _finite(payload.get("valid_stop_hz"), "valid_stop_hz")
            ),
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
        ("Device family", "device_family"), ("Adapter", "adapter_id"),
        ("Device identity", "device_identity_key"), ("Firmware", "firmware_fingerprint"),
        ("Temperature range", "temperature_range_c"),
    )
    rows = []
    for label, name in fields:
        expected = getattr(profile.signature, name)
        actual = getattr(settings, name) if settings is not None else None
        if name in _REQUIRED_PROVENANCE_FIELDS:
            matches = (
                isinstance(expected, str)
                and isinstance(actual, str)
                and expected.casefold() != "unknown"
                and actual.casefold() != "unknown"
                and expected == actual
            )
        elif name == "temperature_range_c":
            matches = settings is not None and expected == actual
        elif isinstance(expected, (int, float)):
            matches = (
                not isinstance(actual, bool)
                and isinstance(actual, (int, float))
                and math.isclose(expected, actual, abs_tol=1e-6, rel_tol=1e-9)
            )
        else:
            matches = settings is not None and expected == actual
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


__all__ = ["ApplicabilityRow", "CalibratedArray", "CalibrationApplicability", "CalibrationImportPreview", "CalibrationPoint", "CalibrationProfile", "CalibrationProfileError", "CalibrationSignature", "CalibrationStatus", "MeasurementQuality", "MeasurementValue", "apply_calibration", "check_applicability", "preview_calibration_csv", "validate_calibration_profile_id"]
