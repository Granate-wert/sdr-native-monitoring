"""Qt-free presenter for the Diagnostics workspace.

The GUI thread calls only ``refresh`` / ``run_self_tests`` / ``run_validation`` /
``cancel_validation`` / ``export_support_bundle`` / ``poll`` / ``close``.  Heavy
work (offline P15 validation, bundle export) runs on ``threading.Thread`` workers
owned here; ``poll()`` is a cheap cached read safe for a 60 Hz GUI timer.

Privacy invariant: the support bundle contains only structure summaries,
backend/build metadata and validation results.  It never includes I/Q samples,
calibration profile contents or any absolute user path beyond a deliberately
basename-redacted form.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..sdr.native_api import available_backends, native_availability, run_self_test
from ..sdr.validation import run_offline_validation
from .diagnostics_state import (
    DiagnosticsSectionSnapshot,
    DiagnosticsSnapshot,
    SupportBundleSnapshot,
    ValidationRowSnapshot,
    ValidationRunState,
)

_BUNDLE_SCHEMA = "sdr-native-support-bundle"
_BUNDLE_SCHEMA_VERSION = 1


def _fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ") if value >= 100_000 else str(value)


def _basename(text: str | None) -> str:
    if not text:
        return "—"
    cleaned = text.replace("\\", "/").rstrip("/")
    return cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned


def _redact(value: Any) -> Any:
    """Best-effort redaction: turn absolute-looking paths into basenames."""

    if isinstance(value, str) and ("/" in value or "\\" in value):
        return _basename(value)
    if isinstance(value, Mapping):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class DiagnosticsPresenter:
    """Collects platform/native diagnostics and runs safe background checks."""

    def __init__(
        self,
        *,
        validation_runner: Any = None,
        bundle_dir: str | Path | None = None,
    ) -> None:
        self._validation_runner = validation_runner or run_offline_validation
        self._bundle_dir = Path(bundle_dir) if bundle_dir is not None else Path(tempfile.gettempdir())

        self._lock = threading.Lock()
        self._generation = 0
        self._sections: tuple[DiagnosticsSectionSnapshot, ...] = ()
        self._last_error: str | None = None

        self._validation_state = ValidationRunState.IDLE
        self._validation_rows: tuple[ValidationRowSnapshot, ...] = ()
        self._validation_thread: threading.Thread | None = None
        self._validation_cancel = threading.Event()

        self._bundle: SupportBundleSnapshot | None = None
        self._bundle_thread: threading.Thread | None = None

        self.refresh()

    # -- properties ---------------------------------------------------------

    @property
    def snapshot(self) -> DiagnosticsSnapshot:
        with self._lock:
            return self._rebuild_locked()

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def validation_running(self) -> bool:
        with self._lock:
            return self._validation_state is ValidationRunState.RUNNING

    # -- diagnostics sections ------------------------------------------------

    def refresh(self) -> DiagnosticsSnapshot:
        """Rebuild platform/native/backend sections synchronously (cheap)."""

        try:
            sections = self._collect_sections()
        except Exception as exc:  # native probe must degrade, not raise
            sections = (
                DiagnosticsSectionSnapshot(
                    title="Native core",
                    rows=(("status", "unavailable"), ("error", str(exc))),
                ),
            )
        with self._lock:
            self._sections = sections
            self._last_error = None
            self._generation += 1
            return self._rebuild_locked()

    @staticmethod
    def _rows_from_mapping(prefix: str, value: Any) -> tuple[tuple[str, str], ...]:
        rows: list[tuple[str, str]] = []
        if isinstance(value, Mapping):
            for key, item in sorted(value.items()):
                label = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(item, Mapping):
                    rows.extend(DiagnosticsPresenter._rows_from_mapping(label, item))
                else:
                    rows.append((label, str(item)))
        else:
            rows.append((prefix, str(value)))
        return tuple(rows)

    def _collect_sections(self) -> tuple[DiagnosticsSectionSnapshot, ...]:
        import platform

        sections: list[DiagnosticsSectionSnapshot] = []
        sections.append(
            DiagnosticsSectionSnapshot(
                title="Platform",
                rows=(
                    ("os", platform.platform(aliased=True)),
                    ("python", platform.python_version()),
                    ("architecture", platform.machine()),
                    ("cpu_count", str(os.cpu_count())),
                ),
            )
        )

        availability = native_availability()
        native_rows: list[tuple[str, str]] = [("available", str(availability.available))]
        if availability.reason:
            native_rows.append(("status", availability.reason))
        for key, item in sorted(availability.build_info.items()):
            native_rows.append((f"build.{key}", str(item)))
        sections.append(DiagnosticsSectionSnapshot(title="Native core", rows=tuple(native_rows)))

        backend_rows: list[tuple[str, str]] = []
        try:
            backends = available_backends()
        except Exception as exc:
            backend_rows.append(("error", str(exc)))
        else:
            backend_rows.append(("available", ", ".join(backends) if backends else "—"))
        backend_rows.extend(
            (
                ("cuda_compiled", str(bool(availability.build_info.get("cuda_compiled", False)))),
            )
        )
        sections.append(DiagnosticsSectionSnapshot(title="Backends", rows=tuple(backend_rows)))
        return tuple(sections)

    # -- safe self-tests -----------------------------------------------------

    def run_self_tests(self) -> list[str]:
        """Run the small native bootstrap self-test.  Returns one message."""

        try:
            result = run_self_test()
        except Exception as exc:
            message = f"native self-test unavailable: {exc}"
            with self._lock:
                self._last_error = message
                self._generation += 1
            return [message]
        ok = bool(result.get("ok", False))
        detail = str(result.get("message", ""))
        message = f"native self-test: {'ok' if ok else 'FAILED'} — {detail}"
        with self._lock:
            self._last_error = None if ok else message
            self._generation += 1
        return [message]

    # -- P15 validation runner ----------------------------------------------

    def run_validation(self, *, benchmark_repeats: int = 1, recording_blocks: int = 16) -> list[str]:
        """Run offline P15 validation in a bounded worker (cancellable)."""

        with self._lock:
            if self._validation_state is ValidationRunState.RUNNING:
                return ["validation is already running"]
            self._validation_state = ValidationRunState.RUNNING
            self._validation_rows = ()
            self._last_error = None
            self._generation += 1
        self._validation_cancel.clear()
        thread = threading.Thread(
            target=self._validation_worker,
            args=(benchmark_repeats, recording_blocks),
            name="p16-validation",
            daemon=True,
        )
        self._validation_thread = thread
        thread.start()
        return []

    def _validation_worker(self, benchmark_repeats: int, recording_blocks: int) -> None:
        try:
            report = self._validation_runner(
                benchmark_repeats=benchmark_repeats, recording_blocks=recording_blocks
            )
        except Exception as exc:
            with self._lock:
                self._validation_state = ValidationRunState.FAILED
                self._last_error = str(exc)
                self._generation += 1
            return
        if self._validation_cancel.is_set():
            with self._lock:
                self._validation_state = ValidationRunState.IDLE
                self._generation += 1
            return
        rows = tuple(
            ValidationRowSnapshot(
                name=str(item.name),
                status=str(item.status.value),
                detail=str(item.reason or _fmt_int(0)),
            )
            for item in report.results
        )
        with self._lock:
            self._validation_rows = rows
            self._validation_state = ValidationRunState.COMPLETED
            self._generation += 1

    def cancel_validation(self) -> None:
        with self._lock:
            if self._validation_state is not ValidationRunState.RUNNING:
                return
            self._validation_state = ValidationRunState.CANCELLING
            self._generation += 1
        self._validation_cancel.set()

    # -- support bundle ------------------------------------------------------

    def export_support_bundle(self, output_dir: str | Path | None = None) -> list[str]:
        """Write an anonymized JSON support bundle.  Runs on a worker thread."""

        with self._lock:
            if self._bundle_thread is not None and self._bundle_thread.is_alive():
                return ["support bundle export is already running"]
        root = Path(output_dir) if output_dir is not None else self._bundle_dir
        thread = threading.Thread(
            target=self._bundle_worker, args=(root,), name="p16-bundle", daemon=True
        )
        self._bundle_thread = thread
        thread.start()
        return []

    def _bundle_worker(self, root: Path) -> None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            availability = native_availability()
            payload = {
                "schema": _BUNDLE_SCHEMA,
                "schema_version": _BUNDLE_SCHEMA_VERSION,
                "platform": _redact(self._platform_payload()),
                "native": {
                    "available": availability.available,
                    "reason": availability.reason,
                    "build_info": _redact(dict(availability.build_info)),
                },
                "validation": {
                    "state": self._validation_state.value,
                    "rows": [
                        {"name": row.name, "status": row.status, "detail": row.detail}
                        for row in self._validation_rows
                    ],
                },
            }
            final = root / "support_bundle.json"
            part = final.with_suffix(".json.part")
            with part.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(part, final)
            size = final.stat().st_size
        except OSError as exc:
            with self._lock:
                self._bundle = SupportBundleSnapshot(error=str(exc))
                self._last_error = str(exc)
                self._generation += 1
            return
        with self._lock:
            self._bundle = SupportBundleSnapshot(
                path_hint=_basename(str(final)),
                file_count="1",
                size=_fmt_int(size) + " B",
                error=None,
            )
            self._last_error = None
            self._generation += 1

    @staticmethod
    def _platform_payload() -> dict[str, object]:
        import platform

        return {
            "os": platform.platform(aliased=True),
            "python": platform.python_version(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
        }

    # -- polling / lifecycle --------------------------------------------------

    def poll(self) -> DiagnosticsSnapshot:
        with self._lock:
            return self._rebuild_locked()

    def _rebuild_locked(self) -> DiagnosticsSnapshot:
        return DiagnosticsSnapshot(
            generation=self._generation,
            sections=self._sections,
            validation_state=self._validation_state,
            validation_rows=self._validation_rows,
            support_bundle=self._bundle,
            hardware_confirmed=False,
            error=self._last_error,
            stale=False,
        )

    def close(self) -> None:
        """Cancel validation and join workers with a bounded timeout."""

        self.cancel_validation()
        self._validation_cancel.set()
        for thread in (self._validation_thread, self._bundle_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)


__all__ = ["DiagnosticsPresenter"]
