"""Thread-safe standalone calibration service used by S07 UI."""

from __future__ import annotations

import csv
import io
import threading
from pathlib import Path
from typing import Any

from ..domain.calibration import (
    CalibrationApplicability,
    CalibrationImportPreview,
    CalibrationProfile,
    CalibrationProfileError,
    CalibrationSignature,
    MeasurementValue,
    preview_calibration_csv,
)
from .calibration_store import CalibrationProfileStore


class CalibrationService:
    """Profile lifecycle, applicability and unit-safe measurement helpers.

    No hardware is accessed here.  A hardware adapter can provide a current
    signature later, while the same immutable profile and applicability rules
    remain in force.
    """

    def __init__(self, store: CalibrationProfileStore | None = None) -> None:
        self.store = store or CalibrationProfileStore(Path.cwd() / "calibration_profiles")
        self._lock = threading.RLock()
        self._current = CalibrationSignature()
        self._active: CalibrationProfile | None = None

    def list_profiles(self) -> tuple[CalibrationProfile, ...]:
        with self._lock:
            return self.store.list_profiles()

    def set_current_settings(self, settings: CalibrationSignature) -> CalibrationSignature:
        with self._lock:
            self._current = settings
            if self._active is not None and not self.compare_applicability(self._active, settings):
                self._active = None
            return settings

    def current_settings(self) -> CalibrationSignature:
        with self._lock:
            return self._current

    def compare_applicability(self, profile: CalibrationProfile, current: CalibrationSignature | None = None) -> bool:
        return self.applicability(profile, current).applicable

    def applicability(self, profile: CalibrationProfile, current: CalibrationSignature | None = None) -> CalibrationApplicability:
        from ..domain.calibration import check_applicability
        with self._lock:
            return check_applicability(profile, current or self._current)

    def preview_csv(self, data: str, profile_id: str = "imported", profile_version: int = 1) -> CalibrationImportPreview:
        """Preview CSV text without creating a file or profile."""
        if "\n" not in data and "\r" not in data:
            return preview_calibration_csv(Path(data), profile_id, profile_version)
        errors: list[str] = []
        points = []
        try:
            rows = csv.DictReader(io.StringIO(data))
            required = {"frequency_hz", "correction_db", "uncertainty_db"}
            if not required.issubset(rows.fieldnames or ()):
                errors.append("CSV must contain frequency_hz, correction_db and uncertainty_db")
            else:
                from ..domain.calibration import CalibrationPoint
                for row_number, row in enumerate(rows, 2):
                    try:
                        points.append(CalibrationPoint(
                            float(row["frequency_hz"]),
                            float(row["correction_db"]),
                            float(row["uncertainty_db"]),
                            float(row.get("reference_dbm") or 0.0),
                            float(row.get("measured_dbfs") or 0.0),
                        ))
                    except (TypeError, ValueError, CalibrationProfileError) as error:
                        errors.append(f"row {row_number}: {error}")
        except csv.Error as error:
            errors.append(str(error))
        if not errors and (len(points) < 2 or any(left.frequency_hz >= right.frequency_hz for left, right in zip(points, points[1:]))):
            errors.append("points must be strictly increasing and contain at least two rows")
        return CalibrationImportPreview(not errors, "<text>", profile_id, profile_version, tuple(points) if not errors else (), tuple(errors))

    def preview_csv_path(self, path: Path, profile_id: str, profile_version: int) -> CalibrationImportPreview:
        return preview_calibration_csv(Path(path), profile_id, profile_version)

    def finalize_profile(self, profile: CalibrationProfile) -> CalibrationProfile:
        with self._lock:
            return self.store.save(profile)

    def finalize_preview(
        self,
        preview: CalibrationImportPreview,
        signature: CalibrationSignature | None = None,
        *,
        reference_equipment: str = "",
        notes: str = "",
    ) -> CalibrationProfile:
        if not preview.valid:
            raise CalibrationProfileError("cannot finalize an invalid CSV preview")
        profile = CalibrationProfile(
            profile_id=preview.profile_id,
            profile_version=preview.profile_version,
            signature=signature or self._current,
            points=preview.points,
            reference_equipment=reference_equipment,
            notes=notes,
        )
        return self.finalize_profile(profile)

    def select_active_profile(self, profile: CalibrationProfile, *, expert_override: bool = False) -> CalibrationApplicability:
        with self._lock:
            result = self.applicability(profile)
            if not result.applicable and not expert_override:
                raise CalibrationProfileError(f"profile is incompatible: {result.reason}; explicit expert override required")
            self._active = profile
            return result

    def clear_active_profile(self) -> None:
        with self._lock:
            self._active = None

    def active_profile(self) -> CalibrationProfile | None:
        with self._lock:
            return self._active

    def make_measurement(self, measurement_id: str, title: str, value: float | None, unit: str, *, quality: Any, uncertainty_db: float | None, frame_sequence: int | None, config_generation: int | None, source_id: str, calibration_status: Any, warning: str = "") -> MeasurementValue:
        return MeasurementValue(measurement_id, title, value, unit, quality, uncertainty_db, frame_sequence, config_generation, source_id, calibration_status, warning)


__all__ = ["CalibrationService"]
