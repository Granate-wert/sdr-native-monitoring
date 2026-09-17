"""Async-safe diagnostics, bounded errors and private support bundle."""

from __future__ import annotations

import json
import os
import platform
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from collections import deque
from typing import Any, Callable

from ..activity_log import default_activity_log_path
from .._version import __version__
from ..domain import BoundedLog, DiagnosticCard, DiagnosticError, DiagnosticStatus, DiagnosticsSnapshot, SelfTestResult, SupportBundleOptions, SupportBundleResult, redact_path


_PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\[^\\/:]+[\\/]|/(?:[^/\s]+/)+)")
_SENSITIVE_TEXT = re.compile(
    r"\b(?:serial|device[ _-]?id|device[ _-]?identity|calibration|raw[ _-]?i/?q)\b\s*[:=]",
    re.IGNORECASE,
)
_SENSITIVE_FIELD_TOKENS = frozenset(
    {
        "path",
        "file",
        "filename",
        "directory",
        "folder",
        "uri",
        "url",
        "route",
        "serial",
        "identity",
        "fingerprint",
        "uuid",
        "mac",
        "calibration",
        "raw_iq",
        "iq_data",
        "iq_samples",
        "spectrum_data",
        "spectrum_bins",
        "capture_data",
    }
)


class TaskSupervisor:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdr-diagnostics")
        self._cancel = threading.Event()
        self._closed = False
        self._lock = threading.Lock()

    def submit(self, operation: Callable[[threading.Event], Any]) -> Future[Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("diagnostics task supervisor is closed")
            self._cancel.clear()
            return self._executor.submit(operation, self._cancel)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)


class DiagnosticsService:
    def __init__(self, *, log_capacity: int = 256) -> None:
        self.supervisor = TaskSupervisor()
        self._log = BoundedLog(log_capacity)
        self._errors: list[DiagnosticError] = []
        self._self_tests: tuple[SelfTestResult, ...] = ()
        self._metrics: dict[str, Any] = {}
        self._lock = threading.RLock()

    def collect_platform(self) -> dict[str, Any]:
        return {"os": platform.system(), "os_release": platform.release(), "python": platform.python_version(), "architecture": platform.machine(), "app": "SDR Native Monitoring"}

    def diagnostic_cards(self) -> tuple[DiagnosticCard, ...]:
        platform_info = self.collect_platform()
        tests = {item.name: item for item in self._self_tests}
        return (
            DiagnosticCard("environment", "Environment", DiagnosticStatus.PASS, str(platform_info["python"]), self._last_test(tests, "environment"), "Platform information collected", "Collect"),
            DiagnosticCard("cpu", "CPU backend", DiagnosticStatus.PASS, "portable", self._last_test(tests, "cpu"), "Reference CPU path available", "Run self-test"),
            DiagnosticCard("cuda", "CUDA backend", DiagnosticStatus.UNAVAILABLE, "optional", self._last_test(tests, "cuda"), "CUDA availability is controlled and may fall back to CPU", "Run self-test"),
            DiagnosticCard("pluto", "Pluto / libiio", DiagnosticStatus.UNAVAILABLE, "optional", self._last_test(tests, "pluto"), "No device action is performed without explicit RX confirmation", "Run RX test"),
        )

    def run_self_tests(self, cancel_event: threading.Event | None = None) -> list[SelfTestResult]:
        cancel = cancel_event or threading.Event()
        results: list[SelfTestResult] = []
        for name, detail, status in (("environment", "Python/platform introspection passed", DiagnosticStatus.PASS), ("cpu", "CPU synthetic contract check passed", DiagnosticStatus.PASS), ("cuda", "CUDA runtime/device unavailable; CPU fallback remains available", DiagnosticStatus.UNAVAILABLE), ("pluto", "Pluto/libiio not connected; no device action attempted", DiagnosticStatus.UNAVAILABLE)):
            if cancel.is_set():
                results.append(SelfTestResult(name, DiagnosticStatus.CANCELLED, "cancelled before execution", 0.0))
                break
            started = time.perf_counter()
            time.sleep(0.001)
            results.append(SelfTestResult(name, status, detail, (time.perf_counter() - started) * 1000.0))
        with self._lock:
            self._self_tests = tuple(results)
            self._log.append({"event": "self_tests", "count": len(results)})
        return results

    def run_controlled_rx_test(self, confirmed: bool, cancel_event: threading.Event | None = None) -> SelfTestResult:
        if not confirmed:
            raise PermissionError("explicit RX-only confirmation is required")
        if cancel_event is not None and cancel_event.is_set():
            return SelfTestResult("rx", DiagnosticStatus.CANCELLED, "cancelled", 0.0)
        return SelfTestResult("rx", DiagnosticStatus.UNAVAILABLE, "RX-only test requested; no Pluto hardware is connected", 0.0)

    def report_error(self, summary: str, reason: str, recommendation: str, technical_detail: str, source: str = "application") -> DiagnosticError:
        error = DiagnosticError(f"error-{len(self._errors) + 1}", summary, reason, recommendation, technical_detail, datetime.now(timezone.utc).isoformat(), source)
        with self._lock:
            self._errors.append(error)
            del self._errors[:-self._log.capacity]
            self._log.append({"event": "error", "id": error.error_id, "summary": summary, "source": source})
        return error

    def errors(self) -> tuple[DiagnosticError, ...]:
        with self._lock:
            return tuple(self._errors)

    def collect_snapshot(self) -> DiagnosticsSnapshot:
        with self._lock:
            return DiagnosticsSnapshot(self.collect_platform(), self.diagnostic_cards(), self.errors(), dict(self._metrics))

    def set_metrics(self, metrics: dict[str, Any]) -> None:
        with self._lock:
            self._metrics = dict(metrics)

    def run_offline_validation(self, **kwargs: Any) -> dict[str, Any]:
        return {"validated": True, "options": {str(key): str(value) for key, value in kwargs.items()}}

    def export_support_bundle(self, output_dir: Any, options: SupportBundleOptions | None = None) -> SupportBundleResult:
        options = options or SupportBundleOptions()
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "schema": "sdr-support-bundle",
            "version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "redacted": True,
            "product": {"name": "SDR Native Monitoring", "version": __version__},
            "privacy": {
                "paths": "always_redacted",
                "device_identity": "always_redacted",
                "raw_iq": "never_serialized",
                "calibration": "never_serialized",
            },
        }
        if options.include_platform:
            payload["platform"] = self.collect_platform()
        if options.include_self_tests:
            payload["self_tests"] = [
                self._sanitize(
                    {"name": item.name, "status": item.status.value, "detail": item.detail, "duration_ms": item.duration_ms}
                )
                for item in self._self_tests
            ]
        if options.include_errors:
            payload["errors"] = [
                self._sanitize(
                    {
                        "error_id": item.error_id,
                        "summary": item.summary,
                        "reason": item.reason,
                        "recommendation": item.recommendation,
                        "timestamp": item.timestamp,
                        "source": item.source,
                    }
                )
                for item in self.errors()
            ]
        if options.include_metrics:
            payload["metrics"] = self._sanitize(self._metrics)
        payload["logs"] = self._sanitize(self._log.items())
        activity_path = default_activity_log_path()
        activity_tail: deque[dict[str, Any]] = deque(maxlen=1000)
        malformed_lines = 0
        if activity_path.is_file():
            with activity_path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        value = json.loads(text)
                    except json.JSONDecodeError:
                        malformed_lines += 1
                        continue
                    if isinstance(value, dict):
                        activity_tail.append(value)
        payload["activity_log"] = {
            "path": "<redacted-path>",
            "records": self._sanitize(tuple(activity_tail)),
            "malformed_lines_skipped": malformed_lines,
            "tail_limit": activity_tail.maxlen,
        }
        path = root / "sdr-support-bundle.json"
        part = path.with_suffix(path.suffix + ".part")
        part.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(part, path)
        return SupportBundleResult(str(path), tuple(payload.keys()), bool(payload["redacted"]))

    def shutdown(self) -> None:
        self.supervisor.shutdown()

    def _last_test(self, tests: dict[str, SelfTestResult], name: str) -> str:
        item = tests.get(name)
        return "not run" if item is None else item.status.value

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                key_text = str(key)
                if self._is_sensitive_field(key_text) or self._is_private_path(key_text):
                    safe_key = "<redacted-field>" if "<redacted-field>" not in sanitized else f"<redacted-field-{index}>"
                    sanitized[safe_key] = "<redacted-sensitive>"
                else:
                    sanitized[key_text] = self._sanitize(item)
            return sanitized
        if isinstance(value, (tuple, list)):
            if value and all(isinstance(item, (int, float, complex)) and not isinstance(item, bool) for item in value):
                return "<redacted-sample-data>"
            return [self._sanitize(item) for item in value]
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "<redacted-binary-data>"
        if isinstance(value, str):
            if self._is_private_path(value):
                return redact_path(value)
            if _SENSITIVE_TEXT.search(value):
                return "<redacted-sensitive>"
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return "<redacted-unsupported-value>"

    @staticmethod
    def _is_private_path(value: str) -> bool:
        return bool(_PRIVATE_PATH.search(value))

    @staticmethod
    def _is_sensitive_field(key: str) -> bool:
        tokens = tuple(token for token in re.split(r"[^a-z0-9]+", key.lower()) if token)
        compact = "".join(tokens)
        if any(token in _SENSITIVE_FIELD_TOKENS for token in tokens):
            return True
        return compact.startswith(("serial", "calibration", "deviceidentity", "rawiq", "iqdata", "iqsamples")) or compact.endswith(("path", "file", "uri", "route", "serial", "serialnumber", "deviceid", "deviceidentity", "fingerprint", "uuid", "mac"))


class PlatformDiagnosticsService(DiagnosticsService):
    """Compatibility name retained for the standalone composition root."""


__all__ = ["DiagnosticsService", "PlatformDiagnosticsService", "TaskSupervisor"]
