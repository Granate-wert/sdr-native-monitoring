"""Asynchronous calibration profile presentation for S07."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ...domain import CalibrationProfile, CalibrationSignature
from ...services.interfaces import CalibrationSdrService


class CalibrationPresenter(QObject):
    profiles_changed = Signal(object)
    preview_changed = Signal(object)
    applicability_changed = Signal(object)
    active_changed = Signal(object)
    busy_changed = Signal(bool)
    task_failed = Signal(str)

    def __init__(self, service: CalibrationSdrService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdr-calibration")
        self._closed = False

    def refresh(self) -> None:
        self._submit(self._service.list_profiles, self.profiles_changed.emit)

    def set_current_settings(self, settings: CalibrationSignature) -> None:
        self._submit(lambda: self._service.set_current_settings(settings), self.applicability_changed.emit)

    def compare(self, profile: CalibrationProfile) -> None:
        self._submit(lambda: self._service.applicability(profile), self.applicability_changed.emit)

    def preview_path(self, path: Path, profile_id: str, profile_version: int) -> None:
        self._submit(lambda: self._service.preview_csv_path(path, profile_id, profile_version), self.preview_changed.emit)

    def preview_text(self, data: str, profile_id: str, profile_version: int) -> None:
        self._submit(lambda: self._service.preview_csv(data, profile_id, profile_version), self.preview_changed.emit)

    def finalize_preview(self, preview: Any, signature: CalibrationSignature | None = None) -> None:
        self._submit(lambda: self._service.finalize_preview(preview, signature), self._finalized)

    def activate(self, profile: CalibrationProfile, *, expert_override: bool = False) -> None:
        self._submit(lambda: self._service.select_active_profile(profile, expert_override=expert_override), self.active_changed.emit)

    def clear_active(self) -> None:
        self._submit(self._service.clear_active_profile, lambda _unused: self.active_changed.emit(None))

    def shutdown(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _finalized(self, profile: CalibrationProfile) -> None:
        self.profiles_changed.emit(self._service.list_profiles())
        self.active_changed.emit(profile)

    def _submit(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        if self._closed:
            return
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete(completed, on_success))

    def _complete(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            value = future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self.busy_changed.emit(False)


__all__ = ["CalibrationPresenter"]
