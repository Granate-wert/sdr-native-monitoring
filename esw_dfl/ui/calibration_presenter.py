"""Qt-free presenter for calibration profile browsing and applicability."""

from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path
from typing import Any, cast

from ..sdr.calibration_store import (
    CalibrationApplicationStatus,
    CalibrationProfile,
    CalibrationProfileError,
    CalibrationProfileStore,
    CalibrationSignature,
    check_applicability,
    profile_from_csv,
)
from .calibration_state import (
    CalibrationComparisonRow,
    CalibrationImportPreview,
    CalibrationPlotSnapshot,
    CalibrationProfileSnapshot,
    CalibrationWorkspaceSnapshot,
)
from .i18n import LocaleId
from .units import format_frequency_hz, format_level


class CalibrationPresenter:
    """Own the control-plane profile workflow, never the native FFT loop."""

    def __init__(
        self,
        store: CalibrationProfileStore | None = None,
        *,
        locale: LocaleId = LocaleId.RU,
        settings: CalibrationSignature | None = None,
        frequency_hz: float | None = None,
        allow_extrapolation: bool = False,
    ) -> None:
        self._store = store or CalibrationProfileStore(Path("calibration_profiles"))
        self._locale = locale
        self._settings = settings
        self._frequency_hz = frequency_hz
        self._allow_extrapolation = bool(allow_extrapolation)
        self._profiles: tuple[CalibrationProfile, ...] = ()
        self._selected_key: tuple[str, int] | None = None
        self._active_key: tuple[str, int] | None = None
        self._preview_profile: CalibrationProfile | None = None
        self._snapshot = CalibrationWorkspaceSnapshot()
        self.refresh()

    @property
    def snapshot(self) -> CalibrationWorkspaceSnapshot:
        return self._snapshot

    @property
    def profiles(self) -> tuple[CalibrationProfile, ...]:
        return self._profiles

    @property
    def active_profile(self) -> CalibrationProfile | None:
        return self._find(self._active_key)

    @property
    def selected_profile(self) -> CalibrationProfile | None:
        return self._find(self._selected_key)

    def refresh(self) -> CalibrationWorkspaceSnapshot:
        try:
            profiles = self._store.list_profiles()
        except (CalibrationProfileError, OSError) as exc:
            self._profiles = ()
            self._snapshot = CalibrationWorkspaceSnapshot(error=str(exc))
            return self._snapshot
        self._profiles = profiles
        valid_keys = {(item.profile_id, item.profile_version) for item in profiles}
        if self._selected_key not in valid_keys:
            self._selected_key = next(iter(valid_keys), None)
        if self._active_key not in valid_keys:
            self._active_key = None
        return self._rebuild()

    def set_current_settings(self, settings: CalibrationSignature | None) -> CalibrationWorkspaceSnapshot:
        self._settings = settings
        return self._rebuild()

    def set_frequency(self, frequency_hz: float | None, *, allow_extrapolation: bool | None = None) -> CalibrationWorkspaceSnapshot:
        if frequency_hz is not None and (not math.isfinite(float(frequency_hz)) or float(frequency_hz) <= 0.0):
            raise ValueError("frequency_hz must be positive and finite")
        self._frequency_hz = None if frequency_hz is None else float(frequency_hz)
        if allow_extrapolation is not None:
            self._allow_extrapolation = bool(allow_extrapolation)
        return self._rebuild()

    def select_profile(self, profile_id: str, profile_version: int | None = None) -> bool:
        profile = self._find((profile_id, profile_version)) if profile_version is not None else self._latest(profile_id)
        if profile is None:
            self._snapshot = self._with_error("calibration profile was not found")
            return False
        self._selected_key = (profile.profile_id, profile.profile_version)
        self._rebuild()
        return True

    def select_active_profile(self) -> tuple[bool, str | None]:
        profile = self.selected_profile
        if profile is None:
            message = "Выберите профиль перед активацией"
            self._snapshot = self._with_error(message)
            return False, message
        applicability = check_applicability(
            profile,
            self._settings,
            frequency_hz=self._frequency_hz,
            allow_extrapolation=self._allow_extrapolation,
        )
        if not applicability.applicable:
            message = applicability.reason
            self._snapshot = self._with_error(message)
            return False, message
        self._active_key = (profile.profile_id, profile.profile_version)
        self._rebuild()
        return True, None

    def clear_active_profile(self) -> CalibrationWorkspaceSnapshot:
        self._active_key = None
        return self._rebuild()

    def preview_csv(
        self,
        path: str | Path,
        *,
        profile_id: str,
        profile_version: int,
        signature: CalibrationSignature | None = None,
        reference_equipment: str = "",
        notes: str = "",
        valid_start_hz: float | None = None,
        valid_stop_hz: float | None = None,
    ) -> CalibrationImportPreview:
        source_name = Path(path).name
        active_signature = signature or self._settings
        try:
            if active_signature is None:
                raise CalibrationProfileError("signature is required before importing a profile")
            profile = profile_from_csv(
                path,
                profile_id=profile_id,
                profile_version=profile_version,
                signature=active_signature,
                reference_equipment=reference_equipment,
                notes=notes,
                valid_start_hz=valid_start_hz,
                valid_stop_hz=valid_stop_hz,
            )
        except (CalibrationProfileError, OSError, UnicodeError) as exc:
            preview = CalibrationImportPreview(
                source_name=source_name,
                profile_id=profile_id,
                profile_version=profile_version,
                valid=False,
                errors=(str(exc),),
            )
            self._preview_profile = None
            self._snapshot = CalibrationWorkspaceSnapshot(
                profiles=self._snapshot.profiles,
                selected_profile_id=self._snapshot.selected_profile_id,
                selected_profile_version=self._snapshot.selected_profile_version,
                active_profile_id=self._snapshot.active_profile_id,
                active_profile_version=self._snapshot.active_profile_version,
                applicability=self._snapshot.applicability,
                applicability_reason=self._snapshot.applicability_reason,
                comparison=self._snapshot.comparison,
                plot=self._snapshot.plot,
                import_preview=preview,
                error=str(exc),
            )
            return preview
        points = tuple((item.frequency_hz, item.correction_db, item.uncertainty_db) for item in profile.points)
        preview = CalibrationImportPreview(
            source_name=source_name,
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            points=points,
            plot=self._plot(profile),
            valid=True,
        )
        self._preview_profile = profile
        self._snapshot = self._replace_snapshot(import_preview=preview, error=None)
        return preview

    def finalize_import(self) -> tuple[bool, str | None]:
        profile = self._preview_profile
        if profile is None or self._snapshot.import_preview is None or not self._snapshot.import_preview.valid:
            message = "Сначала подготовьте корректный CSV-предпросмотр"
            self._snapshot = self._with_error(message)
            return False, message
        try:
            self._store.save(profile)
        except (CalibrationProfileError, OSError) as exc:
            self._snapshot = self._with_error(str(exc))
            return False, str(exc)
        self._selected_key = (profile.profile_id, profile.profile_version)
        self._preview_profile = None
        self.refresh()
        return True, None

    def _find(self, key: tuple[str, int] | None) -> CalibrationProfile | None:
        if key is None:
            return None
        return next((item for item in self._profiles if (item.profile_id, item.profile_version) == key), None)

    def _latest(self, profile_id: str) -> CalibrationProfile | None:
        candidates = [item for item in self._profiles if item.profile_id == profile_id]
        return max(candidates, key=lambda item: item.profile_version, default=None)

    def _rebuild(self) -> CalibrationWorkspaceSnapshot:
        selected = self.selected_profile
        applicability = (
            check_applicability(
                selected,
                self._settings,
                frequency_hz=self._frequency_hz,
                allow_extrapolation=self._allow_extrapolation,
            )
            if selected is not None
            else None
        )
        summaries = tuple(self._summary(profile) for profile in self._profiles)
        self._snapshot = CalibrationWorkspaceSnapshot(
            profiles=summaries,
            selected_profile_id=selected.profile_id if selected else None,
            selected_profile_version=selected.profile_version if selected else None,
            active_profile_id=self._active_key[0] if self._active_key else None,
            active_profile_version=self._active_key[1] if self._active_key else None,
            applicability=applicability.status if applicability else CalibrationApplicationStatus.UNCALIBRATED,
            applicability_reason=applicability.reason if applicability else "Профиль не выбран",
            comparison=self._comparison(selected),
            plot=self._plot(selected) if selected else CalibrationPlotSnapshot(),
            import_preview=self._snapshot.import_preview,
            error=None,
        )
        return self._snapshot

    def _summary(self, profile: CalibrationProfile) -> CalibrationProfileSnapshot:
        applicability = check_applicability(
            profile,
            self._settings,
            frequency_hz=self._frequency_hz,
            allow_extrapolation=self._allow_extrapolation,
        )
        frequencies = [point.frequency_hz for point in profile.points]
        valid_start = profile.valid_start_hz or frequencies[0]
        valid_stop = profile.valid_stop_hz or frequencies[-1]
        uncertainty = [point.uncertainty_db for point in profile.points]
        return CalibrationProfileSnapshot(
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            device_serial=profile.signature.device_serial,
            backend=profile.signature.backend,
            rf_port_path=profile.signature.rf_port_path,
            gain=format_level(profile.signature.manual_gain_db, "dB", locale=self._locale),
            sample_rate=format_frequency_hz(profile.signature.sample_rate_hz, locale=self._locale),
            bandwidth=format_frequency_hz(profile.signature.analog_bandwidth_hz, locale=self._locale),
            valid_range=f"{format_frequency_hz(valid_start, locale=self._locale)} – {format_frequency_hz(valid_stop, locale=self._locale)}",
            reference_plane=profile.reference_plane,
            created_at=profile.created_at,
            uncertainty=(
                f"{format_level(min(uncertainty), 'dB', locale=self._locale)} – {format_level(max(uncertainty), 'dB', locale=self._locale)}"
            ),
            point_count=len(profile.points),
            applicability=applicability.status,
            applicability_reason=applicability.reason,
            active=(profile.profile_id, profile.profile_version) == self._active_key,
        )

    def _comparison(self, profile: CalibrationProfile | None) -> tuple[CalibrationComparisonRow, ...]:
        if profile is None:
            return ()
        expected = profile.signature
        current = self._settings
        fields: tuple[tuple[str, str, str, Callable[[object, object], bool]], ...] = (
            ("device_serial", "Device", "device_serial", lambda a, b: a == b),
            ("backend", "Backend", "backend", lambda a, b: a == b),
            ("rf_port_path", "RF path", "rf_port_path", lambda a, b: a == b),
            ("sample_rate_hz", "Sample rate", "sample_rate_hz", _same_float),
            ("analog_bandwidth_hz", "Bandwidth", "analog_bandwidth_hz", _same_float),
            ("gain_mode", "Gain mode", "gain_mode", lambda a, b: a == b),
            ("manual_gain_db", "Gain", "manual_gain_db", _same_float),
            ("window_normalization_version", "Window normalization", "window_normalization_version", lambda a, b: a == b),
            ("fft_unit_convention", "FFT units", "fft_unit_convention", lambda a, b: a == b),
            ("frontend_chain", "Frontend chain", "frontend_chain", lambda a, b: a == b),
            ("reference_plane", "Reference plane", "reference_plane", lambda a, b: a == b),
        )
        rows: list[CalibrationComparisonRow] = []
        for _, label, attribute, comparator in fields:
            expected_value = getattr(expected, attribute)
            actual_value = getattr(current, attribute) if current is not None else None
            matches = current is not None and comparator(expected_value, actual_value)
            rows.append(
                CalibrationComparisonRow(
                    label,
                    self._signature_value(attribute, expected_value),
                    self._signature_value(attribute, actual_value),
                    matches,
                    None if matches else ("Нет текущей конфигурации" if current is None else "Несовпадение параметра"),
                )
            )
        return tuple(rows)

    def _signature_value(self, attribute: str, value: object) -> str:
        if value is None:
            return "—"
        if attribute in {"sample_rate_hz", "analog_bandwidth_hz"}:
            return format_frequency_hz(float(cast(Any, value)), locale=self._locale)
        if attribute == "manual_gain_db":
            return format_level(float(cast(Any, value)), "dB", locale=self._locale)
        return str(value)

    @staticmethod
    def _plot(profile: CalibrationProfile | None) -> CalibrationPlotSnapshot:
        if profile is None:
            return CalibrationPlotSnapshot()
        return CalibrationPlotSnapshot(
            tuple(item.frequency_hz for item in profile.points),
            tuple(item.correction_db for item in profile.points),
            tuple(item.uncertainty_db for item in profile.points),
        )

    def _replace_snapshot(self, **changes: object) -> CalibrationWorkspaceSnapshot:
        values = {field: getattr(self._snapshot, field) for field in self._snapshot.__dataclass_fields__}
        values.update(changes)
        self._snapshot = CalibrationWorkspaceSnapshot(**values)
        return self._snapshot

    def _with_error(self, message: str) -> CalibrationWorkspaceSnapshot:
        return self._replace_snapshot(error=message)


def _same_float(left: object, right: object) -> bool:
    try:
        return math.isclose(float(cast(Any, left)), float(cast(Any, right)), rel_tol=1e-9, abs_tol=1e-6)
    except (TypeError, ValueError):
        return False


__all__ = ["CalibrationPresenter"]
