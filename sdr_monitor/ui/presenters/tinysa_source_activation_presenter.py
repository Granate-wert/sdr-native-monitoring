"""Single-worker presenter for explicit tinySA source activation."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ...application.tinysa_source_activation import TinySaSourceActivationUseCases
from ...services.tinysa_source_composition import (
    TinySaComposedSource,
    TinySaSourcePhase,
    TinySaSourceSnapshot,
    TinySaVerifiedSource,
)


@dataclass(frozen=True, slots=True)
class TinySaSourceActivationPresenterMetrics:
    operations_started: int
    operations_rejected_while_busy: int
    operation_failures: int
    discover_operations_started: int
    select_operations_started: int
    verify_operations_started: int
    compose_operations_started: int


@dataclass(frozen=True, slots=True)
class TinySaSourceActivationWitness:
    """Route-free scalar transcript of one completed source activation."""

    source: TinySaVerifiedSource
    discover_actions: int
    select_actions: int
    version_verify_actions: int
    compose_actions: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, TinySaVerifiedSource):
            raise TypeError("tinySA activation witness requires a verified source")
        if (
            self.discover_actions,
            self.select_actions,
            self.version_verify_actions,
            self.compose_actions,
        ) != (1, 1, 1, 1):
            raise ValueError("tinySA activation witness requires one explicit transition each")


class TinySaSourceActivationPresenter(QObject):
    """Serialize PnP/version work and expose only route-free snapshots."""

    snapshot_changed = Signal(object)
    composition_ready = Signal(object)
    busy_changed = Signal(bool)
    task_failed = Signal(str)
    _operation_finished = Signal(str, object)

    def __init__(
        self,
        use_cases: TinySaSourceActivationUseCases,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        required = (
            "current",
            "discover",
            "select",
            "verify_selected",
            "compose_selected",
        )
        if any(not callable(getattr(use_cases, name, None)) for name in required):
            raise ValueError("tinySA activation presenter requires complete use cases")
        snapshot = use_cases.current()
        if not isinstance(snapshot, TinySaSourceSnapshot):
            raise TypeError("tinySA activation use cases returned an invalid snapshot")
        self._use_cases = use_cases
        self._snapshot = snapshot
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tinysa-source")
        self._pending = False
        self._closed = False
        self._shutdown_complete = False
        self._operations_started = 0
        self._operations_rejected = 0
        self._operation_failures = 0
        self._discover_operations_started = 0
        self._select_operations_started = 0
        self._verify_operations_started = 0
        self._compose_operations_started = 0
        self._operation_finished.connect(self._complete_operation)

    @property
    def current_snapshot(self) -> TinySaSourceSnapshot:
        return self._snapshot

    @property
    def busy(self) -> bool:
        return self._pending

    @property
    def metrics(self) -> TinySaSourceActivationPresenterMetrics:
        return TinySaSourceActivationPresenterMetrics(
            self._operations_started,
            self._operations_rejected,
            self._operation_failures,
            self._discover_operations_started,
            self._select_operations_started,
            self._verify_operations_started,
            self._compose_operations_started,
        )

    def completed_witness(self) -> TinySaSourceActivationWitness:
        """Return one exact route-free transcript only after composition."""

        snapshot = self._snapshot
        if snapshot.phase is not TinySaSourcePhase.COMPOSED or snapshot.verified is None:
            raise RuntimeError("tinySA activation witness requires a composed source")
        return TinySaSourceActivationWitness(
            source=snapshot.verified,
            discover_actions=self._discover_operations_started,
            select_actions=self._select_operations_started,
            version_verify_actions=self._verify_operations_started,
            compose_actions=self._compose_operations_started,
        )

    def discover(self) -> None:
        self._submit("discover", self._use_cases.discover)

    def select(self, source_id: str) -> None:
        self._submit("select", lambda: self._use_cases.select(source_id))

    def verify_selected(self) -> None:
        self._submit("verify", self._use_cases.verify_selected)

    def compose_selected(self) -> None:
        self._submit("compose", self._use_cases.compose_selected)

    def prepare_shutdown(self) -> None:
        self._closed = True

    def shutdown(self) -> None:
        self.prepare_shutdown()
        self.finish_shutdown()

    def finish_shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._shutdown_complete = True

    def _submit(self, kind: str, operation: object) -> None:
        if self._closed:
            return
        if self._pending:
            self._operations_rejected += 1
            return
        if not callable(operation):
            raise TypeError("tinySA activation operation must be callable")
        self._pending = True
        self._operations_started += 1
        if kind == "discover":
            self._discover_operations_started += 1
        elif kind == "select":
            self._select_operations_started += 1
        elif kind == "verify":
            self._verify_operations_started += 1
        elif kind == "compose":
            self._compose_operations_started += 1
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(
            lambda completed: self._operation_finished.emit(kind, completed)
        )

    def _complete_operation(self, kind: str, future: Future[object]) -> None:
        composition: TinySaComposedSource | None = None
        failure = False
        try:
            result = future.result()
            if kind in ("discover", "select"):
                if not isinstance(result, TinySaSourceSnapshot):
                    raise TypeError("tinySA activation returned an invalid snapshot")
            elif kind == "verify":
                if not isinstance(result, TinySaVerifiedSource):
                    raise TypeError("tinySA verification returned an invalid identity")
            elif kind == "compose":
                if not isinstance(result, TinySaComposedSource):
                    raise TypeError("tinySA activation returned an invalid composition")
                composition = result
            snapshot = self._use_cases.current()
            if not isinstance(snapshot, TinySaSourceSnapshot):
                raise TypeError("tinySA activation returned an invalid current snapshot")
        except Exception:  # noqa: BLE001 - the worker boundary must reduce every operation failure to state.
            failure = True
            self._operation_failures += 1
            try:
                snapshot = self._use_cases.current()
            except Exception:  # noqa: BLE001 - the fallback snapshot is best-effort after a worker failure.
                snapshot = self._snapshot
        self._snapshot = snapshot
        self._pending = False
        self.busy_changed.emit(False)
        self.snapshot_changed.emit(snapshot)
        if failure:
            self.task_failed.emit("tinySA source activation failed")
        elif composition is not None:
            self.composition_ready.emit(composition)


__all__ = [
    "TinySaSourceActivationPresenter",
    "TinySaSourceActivationPresenterMetrics",
    "TinySaSourceActivationWitness",
]
