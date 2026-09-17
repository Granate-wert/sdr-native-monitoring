"""Qt-free diagnostics control use cases with explicit safety boundaries."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import threading
from typing import Any, Mapping, Protocol

from ..domain import DiagnosticError, DiagnosticsSnapshot, SelfTestResult, SupportBundleOptions, SupportBundleResult


class DiagnosticsTaskSupervisorPort(Protocol):
    """Bounded cancellable task admission supplied by diagnostics infrastructure."""

    def submit(self, operation: Any) -> Future[Any]: ...
    def cancel(self) -> None: ...


class DiagnosticsControlPort(Protocol):
    """Infrastructure operations required by diagnostics use cases."""

    @property
    def supervisor(self) -> DiagnosticsTaskSupervisorPort: ...

    def collect_snapshot(self) -> DiagnosticsSnapshot: ...
    def run_self_tests(self, cancel_event: threading.Event | None = None) -> list[SelfTestResult]: ...
    def run_controlled_rx_test(self, confirmed: bool, cancel_event: threading.Event | None = None) -> SelfTestResult: ...
    def run_offline_validation(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def export_support_bundle(self, output_dir: str | Path, options: SupportBundleOptions | None = None) -> SupportBundleResult: ...
    def report_error(self, summary: str, reason: str, recommendation: str, technical_detail: str, source: str = "application") -> DiagnosticError: ...
    def shutdown(self) -> None: ...


class DiagnosticsControlUseCases(Protocol):
    """Diagnostics operations consumed by the Qt presenter."""

    def snapshot(self) -> DiagnosticsSnapshot: ...
    def start_self_tests(self) -> Future[tuple[SelfTestResult, ...]]: ...
    def start_rx_test(self, confirmed: bool) -> Future[tuple[SelfTestResult, ...]]: ...
    def start_validation(self, **options: Any) -> Future[Mapping[str, Any]]: ...
    def start_bundle_export(self, output_dir: str | Path, options: SupportBundleOptions | None = None) -> Future[SupportBundleResult]: ...
    def cancel(self) -> None: ...
    def report_error(self, summary: str, reason: str, recommendation: str, technical_detail: str) -> DiagnosticsSnapshot: ...
    def shutdown(self) -> None: ...


class DiagnosticsControlApplicationService:
    """Owns diagnostic task admission without Qt or widget dependencies."""

    def __init__(self, port: DiagnosticsControlPort) -> None:
        self._port = port

    def snapshot(self) -> DiagnosticsSnapshot:
        return self._port.collect_snapshot()

    def start_self_tests(self) -> Future[tuple[SelfTestResult, ...]]:
        return self._port.supervisor.submit(lambda cancel: tuple(self._port.run_self_tests(cancel)))

    def start_rx_test(self, confirmed: bool) -> Future[tuple[SelfTestResult, ...]]:
        return self._port.supervisor.submit(
            lambda cancel: (self._port.run_controlled_rx_test(confirmed, cancel),)
        )

    def start_validation(self, **options: Any) -> Future[Mapping[str, Any]]:
        return self._port.supervisor.submit(lambda _cancel: self._port.run_offline_validation(**options))

    def start_bundle_export(
        self,
        output_dir: str | Path,
        options: SupportBundleOptions | None = None,
    ) -> Future[SupportBundleResult]:
        return self._port.supervisor.submit(
            lambda _cancel: self._port.export_support_bundle(output_dir, options)
        )

    def cancel(self) -> None:
        self._port.supervisor.cancel()

    def report_error(
        self,
        summary: str,
        reason: str,
        recommendation: str,
        technical_detail: str,
    ) -> DiagnosticsSnapshot:
        self._port.report_error(summary, reason, recommendation, technical_detail)
        return self._port.collect_snapshot()

    def shutdown(self) -> None:
        self._port.shutdown()


__all__ = [
    "DiagnosticsControlApplicationService",
    "DiagnosticsControlPort",
    "DiagnosticsControlUseCases",
    "DiagnosticsTaskSupervisorPort",
]
