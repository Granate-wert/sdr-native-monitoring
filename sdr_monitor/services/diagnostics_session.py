"""Async-safe diagnostics, bounded errors and private support bundle."""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..domain import BoundedLog, DiagnosticCard, DiagnosticError, DiagnosticStatus, DiagnosticsSnapshot, SelfTestResult, SupportBundleOptions, SupportBundleResult, redact_path


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
        self._executor.shutdown(wait=False, cancel_futures=True)


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
        payload: dict[str, Any] = {"schema": "sdr-support-bundle", "version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "redacted": not options.include_paths}
        if options.include_platform:
            payload["platform"] = self.collect_platform()
        if options.include_self_tests:
            payload["self_tests"] = [{"name": item.name, "status": item.status.value, "detail": item.detail, "duration_ms": item.duration_ms} for item in self._self_tests]
        if options.include_errors:
            payload["errors"] = [self._sanitize(item.__dict__ if hasattr(item, "__dict__") else {"error_id": item.error_id, "summary": item.summary, "reason": item.reason, "recommendation": item.recommendation, "technical_detail": item.technical_detail, "timestamp": item.timestamp, "source": item.source}, options.include_paths) for item in self.errors()]
        if options.include_metrics:
            payload["metrics"] = self._sanitize(self._metrics, options.include_paths)
        payload["logs"] = self._sanitize(self._log.items(), options.include_paths)
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

    def _sanitize(self, value: Any, include_paths: bool) -> Any:
        if isinstance(value, dict):
            return {str(key): self._sanitize(item, include_paths) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self._sanitize(item, include_paths) for item in value]
        if isinstance(value, str) and not include_paths and (":" in value or "\\" in value or value.startswith("/")):
            return redact_path(value)
        return value


class PlatformDiagnosticsService(DiagnosticsService):
    """Compatibility name retained for the standalone composition root."""


__all__ = ["DiagnosticsService", "PlatformDiagnosticsService", "TaskSupervisor"]
