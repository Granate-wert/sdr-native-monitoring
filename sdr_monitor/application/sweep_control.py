"""Qt-free plan-first sweep control use cases."""
from __future__ import annotations
from pathlib import Path
from typing import Callable, Protocol
from contextlib import nullcontext
from .analyzer_session import AnalyzerSessionApplicationService
from ..domain import SweepConfiguration, SweepPlan, SweepProgress, SweepResult

class SweepControlPort(Protocol):
    def plan(self, configuration: SweepConfiguration) -> SweepPlan: ...
    def execute(self, configuration: SweepConfiguration, progress: Callable[[SweepProgress], None]) -> SweepResult: ...
    def cancel(self) -> None: ...
    def export_result(self, result: SweepResult, output_path: Path) -> Path: ...
    def close(self) -> None: ...

class SweepControlUseCases(Protocol):
    def plan(self, configuration: SweepConfiguration) -> SweepPlan: ...
    def execute(self, configuration: SweepConfiguration, progress: Callable[[SweepProgress], None]) -> SweepResult: ...
    def cancel(self) -> None: ...
    def export_result(self, result: SweepResult, output_path: Path) -> Path: ...
    def shutdown(self) -> None: ...

class SweepControlApplicationService:
    """Coordinates plan/run/cancel/export without Qt or sweep adapter imports."""
    def __init__(self, port: SweepControlPort, *, analyzer: AnalyzerSessionApplicationService | None = None) -> None:
        self._port = port
        self._analyzer = analyzer
    def plan(self, configuration: SweepConfiguration) -> SweepPlan:
        with self._analyzer.bounded_sweep_operation() if self._analyzer is not None else nullcontext():
            return self._port.plan(configuration)
    def execute(self, configuration: SweepConfiguration, progress: Callable[[SweepProgress], None]) -> SweepResult:
        with self._analyzer.bounded_sweep_operation() if self._analyzer is not None else nullcontext():
            return self._port.execute(configuration, progress)
    def cancel(self) -> None:
        self._port.cancel()
    def export_result(self, result: SweepResult, output_path: Path) -> Path:
        return self._port.export_result(result, output_path)
    def shutdown(self) -> None:
        self._port.cancel()
        self._port.close()

__all__ = ["SweepControlApplicationService", "SweepControlPort", "SweepControlUseCases"]
