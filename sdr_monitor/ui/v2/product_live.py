"""Explicit UI V2 product composition over externally owned public presenters."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from time import time_ns
from typing import Protocol

from .shell.close_lifecycle import CloseLifecycle, CloseState
from .shell.contracts import ClosePort, V2ShellContext
from .shell.placeholders import default_workspace_definitions
from .spectrum.allocation_budget import PresentationAllocationBudget
from .spectrum.projection import SpectrumProjection, SpectrumProjector
from .state.live_view_state import LiveAction
from .view_models.analyzer_view_model import AnalyzerViewModel, AnalyzerViewState, SweepPresentationPort
from .view_models.calibration_view_model import CalibrationProfilePresenterPort, CalibrationProfileViewModel
from .view_models.diagnostics_view_model import (
    DeferredDiagnosticsViewModel,
    DiagnosticsPresenterFactory,
)
from .view_models.live_view_model import LivePresenterPort, LiveViewModel
from .view_models.replay_view_model import DeferredReplayViewModel, ReplayPresenterFactory
from .view_models.sweep_view_model import SweepPresenterPort, SweepViewModel
from .view_models.tinysa_view_model import (
    DeferredTinySaSourceActivationViewModel,
    TinySaAnalyzerBinding,
    TinySaAnalyzerBindingFactory,
    TinySaAnalyzerViewModel,
    TinySaSourceActivationPresenterFactory,
)
from .workspaces import (
    calibration_profiles_workspace_definition,
    diagnostics_workspace_definition,
    home_workspace_definition,
    live_workspace_definition,
    replay_workspace_definition,
    sweep_workspace_definition,
    tinysa_activation_workspace_definition,
    tinysa_analyzer_workspace_definition,
)
from .workspaces.analyzer import analyzer_workspace_definition


class LivePresenterLifecyclePort(LivePresenterPort, Protocol):
    """The existing public presenter plus its explicit application shutdown hook."""

    def shutdown(self) -> None: ...


class SweepPresenterLifecyclePort(SweepPresenterPort, Protocol):
    """The frozen Sweep presenter plus its explicit application shutdown hook."""

    def shutdown(self) -> None: ...


class CalibrationPresenterLifecyclePort(CalibrationProfilePresenterPort, Protocol):
    """The no-write profile browser boundary plus explicit application shutdown."""

    def shutdown(self) -> None: ...


class AnalyzerPresenterLifecyclePort(SweepPresentationPort, Protocol):
    def can_close(self) -> bool: ...
    def shutdown(self) -> None: ...


class _TinySaProductController:
    """Own V2 subscriptions and shutdown around deferred tinySA presenters only."""

    def __init__(
        self,
        presenter_factory: TinySaSourceActivationPresenterFactory,
        analyzer_binding_factory: TinySaAnalyzerBindingFactory,
    ) -> None:
        self.activation_view_model = DeferredTinySaSourceActivationViewModel(
            presenter_factory,
            analyzer_binding_factory,
        )
        self.analyzer_view_model: TinySaAnalyzerViewModel | None = None
        self._shutdown = False

    def workspace_definition(self):
        return tinysa_activation_workspace_definition(
            self.activation_view_model,
            self._analyzer_workspace_definition,
        )

    def can_close(self) -> bool:
        analyzer = self.analyzer_view_model
        return self.activation_view_model.state.can_close and (analyzer is None or analyzer.state.can_close)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.activation_view_model.dispose()
        analyzer = self.analyzer_view_model
        if analyzer is not None:
            analyzer.dispose()
            analyzer.shutdown_presenter()

    def _analyzer_workspace_definition(self, binding: TinySaAnalyzerBinding):
        if self.analyzer_view_model is not None:
            raise RuntimeError("tinySA V2 analyzer is already registered")
        self.analyzer_view_model = TinySaAnalyzerViewModel(binding)
        return tinysa_analyzer_workspace_definition(self.analyzer_view_model)


class V2LiveProductComposition:
    """Own V2 adapters; all receiver work remains behind the supplied presenters."""

    def __init__(
        self,
        presenter: LivePresenterLifecyclePort,
        *,
        sweep_presenter: SweepPresenterLifecyclePort | None = None,
        analyzer_presenter: AnalyzerPresenterLifecyclePort | None = None,
        projection_submit: Callable[[Callable[[], SpectrumProjection]], Future] | None = None,
        persistence_submit: Callable[[Callable[[], object]], Future] | None = None,
        resumable_persistence: bool = False,
        persistence_max_chunks: int = 8,
        allocation_budget: PresentationAllocationBudget | None = None,
        async_shutdown: bool = False,
        calibration_presenter: CalibrationPresenterLifecyclePort | None = None,
        diagnostics_presenter_factory: DiagnosticsPresenterFactory | None = None,
        replay_presenter_factory: ReplayPresenterFactory | None = None,
        tinysa_activation_presenter_factory: TinySaSourceActivationPresenterFactory | None = None,
        tinysa_analyzer_binding_factory: TinySaAnalyzerBindingFactory | None = None,
        now_ns: Callable[[], int] = time_ns,
    ) -> None:
        self._presenter = presenter
        self._sweep_presenter = sweep_presenter
        self.analyzer_presenter = analyzer_presenter
        self.allocation_budget = allocation_budget or PresentationAllocationBudget()
        self.spectrum_projector = (None if projection_submit is None else SpectrumProjector(
            projection_submit, allocation_budget=self.allocation_budget,
            persistence_submit=persistence_submit,
            resumable_persistence=resumable_persistence,
            persistence_max_chunks=persistence_max_chunks))
        self._calibration_presenter = calibration_presenter
        self.view_model = LiveViewModel(presenter, now_ns=now_ns)
        self.analyzer_view_model = (
            AnalyzerViewModel(self.view_model, analyzer_presenter)
            if analyzer_presenter is not None else None
        )
        self._unsubscribe_projection = (
            self.analyzer_view_model.subscribe(self._on_projection_control)
            if self.analyzer_view_model is not None and self.spectrum_projector is not None else None
        )
        # LiveViewModel connected first: its synchronous Analyzer subscribers
        # finish all layers before this same-worker preparation boundary flushes
        # the pending viewport. Sweep owns a different preparation worker and
        # keeps normal timer coalescing, as do chrome-only model notifications.
        self._projection_delivery_signal = (
            getattr(presenter, "prepared_snapshot_ready")
            if self.spectrum_projector is not None and getattr(presenter, "prepares_snapshots", False) is True
            else None
        )
        if self._projection_delivery_signal is not None:
            self._projection_delivery_signal.connect(self._commit_live_projection)
        self._projection_backpressure = getattr(presenter, "set_projection_in_flight", None)
        self._live_preparation_signal = (
            getattr(presenter, "preparation_active_changed", None)
            if self.spectrum_projector is not None else None)
        if self._live_preparation_signal is not None and self.spectrum_projector is not None:
            self._live_preparation_signal.connect(self.spectrum_projector.set_live_preparation_in_flight)
        if self.spectrum_projector is not None and callable(self._projection_backpressure):
            self.spectrum_projector.work_active_changed.connect(self._projection_backpressure)
        self._sweep_projection_backpressure = getattr(analyzer_presenter, "set_projection_in_flight", None)
        if self.spectrum_projector is not None and callable(self._sweep_projection_backpressure):
            self.spectrum_projector.work_active_changed.connect(self._sweep_projection_backpressure)
        self._sweep_preparation_signal = (
            getattr(analyzer_presenter, "poll_preparation_active_changed", None)
            if self.spectrum_projector is not None else None)
        if self._sweep_preparation_signal is not None and self.spectrum_projector is not None:
            self._sweep_preparation_signal.connect(self.spectrum_projector.set_preparation_in_flight)
        self.sweep_view_model = None if sweep_presenter is None else SweepViewModel(sweep_presenter)
        self.calibration_view_model = (
            None if calibration_presenter is None else CalibrationProfileViewModel(calibration_presenter)
        )
        self.diagnostics_view_model = (
            None
            if diagnostics_presenter_factory is None
            else DeferredDiagnosticsViewModel(diagnostics_presenter_factory)
        )
        self.replay_view_model = (
            None if replay_presenter_factory is None else DeferredReplayViewModel(replay_presenter_factory)
        )
        if tinysa_activation_presenter_factory is None and tinysa_analyzer_binding_factory is None:
            self._tinysa = None
        elif tinysa_activation_presenter_factory is not None and tinysa_analyzer_binding_factory is not None:
            self._tinysa = _TinySaProductController(
                tinysa_activation_presenter_factory,
                tinysa_analyzer_binding_factory,
            )
        else:
            raise ValueError("tinySA V2 requires both deferred activation and analyzer factories")
        self._is_shutdown = False
        self._terminal_presentation_released = False
        self.close_lifecycle = CloseLifecycle(self._prepare_async_shutdown) if async_shutdown else None
        self._presentation_disposed = False
        live_definition = live_workspace_definition(self.view_model)
        sweep_definition = None if self.sweep_view_model is None else sweep_workspace_definition(self.sweep_view_model)
        calibration_definition = (
            None
            if self.calibration_view_model is None
            else calibration_profiles_workspace_definition(self.calibration_view_model)
        )
        diagnostics_definition = (
            None
            if self.diagnostics_view_model is None
            else diagnostics_workspace_definition(self.diagnostics_view_model)
        )
        replay_definition = (
            None if self.replay_view_model is None else replay_workspace_definition(self.replay_view_model)
        )
        tinysa_definition = None if self._tinysa is None else self._tinysa.workspace_definition()
        home_definition = home_workspace_definition(
            sweep_available=sweep_definition is not None,
            calibration_available=calibration_definition is not None,
            diagnostics_available=diagnostics_definition is not None,
            tinysa_available=tinysa_definition is not None,
            replay_available=replay_definition is not None,
        )
        workspaces = tuple(
            home_definition
            if definition.workspace_id == "home"
            else live_definition
            if definition.workspace_id == "live"
            else sweep_definition
            if definition.workspace_id == "sweep" and sweep_definition is not None
            else calibration_definition
            if definition.workspace_id == "calibration" and calibration_definition is not None
            else diagnostics_definition
            if definition.workspace_id == "diagnostics" and diagnostics_definition is not None
            else replay_definition
            if definition.workspace_id == "replay" and replay_definition is not None
            else definition
            for definition in default_workspace_definitions()
        )
        if tinysa_definition is not None:
            workspaces += (tinysa_definition,)
        if self.analyzer_view_model is not None:
            # Product Analyzer replaces both old routes, not two pages hidden
            # inside a tab. Their application ownership remains unchanged.
            workspaces = (analyzer_workspace_definition(self.analyzer_view_model, self.spectrum_projector),) + tuple(
                item for item in workspaces if item.workspace_id not in {"home", "live", "sweep"}
            )
        self.context = V2ShellContext(
            workspaces=workspaces,
            initial_workspace_id="analyzer" if self.analyzer_view_model is not None else "home",
            close_ports=(
                ClosePort(
                    name="live-presenter",
                    can_close=self.can_close,
                    shutdown=self.shutdown,
                    request_shutdown=self.request_shutdown if async_shutdown else None,
                    poll_shutdown=self.poll_shutdown if async_shutdown else None,
                ),
            ),
            automatic_discovery_enabled=False,
        )

    def _on_projection_control(self, state: AnalyzerViewState) -> None:
        projector = self.spectrum_projector
        if projector is not None:
            projector.set_suspended(state.live.busy or state.starting or state.stopping)

    def _commit_live_projection(self, _state: object) -> None:
        if self.spectrum_projector is not None:
            self.spectrum_projector.request_commit()

    def _disconnect_projection_delivery(self) -> None:
        if self._live_preparation_signal is not None and self.spectrum_projector is not None:
            self._live_preparation_signal.disconnect(self.spectrum_projector.set_live_preparation_in_flight)
            self._live_preparation_signal = None
        if self._sweep_preparation_signal is not None and self.spectrum_projector is not None:
            self._sweep_preparation_signal.disconnect(self.spectrum_projector.set_preparation_in_flight)
            self._sweep_preparation_signal = None
        if self._projection_delivery_signal is not None:
            self._projection_delivery_signal.disconnect(self._commit_live_projection)
            self._projection_delivery_signal = None
        if self.spectrum_projector is not None and callable(self._projection_backpressure):
            self.spectrum_projector.work_active_changed.disconnect(self._projection_backpressure)
            self._projection_backpressure = None
        if self.spectrum_projector is not None and callable(self._sweep_projection_backpressure):
            self.spectrum_projector.work_active_changed.disconnect(self._sweep_projection_backpressure)
            self._sweep_projection_backpressure = None

    def can_close(self) -> bool:
        """Refuse a silent close while presenter work or a Live stream is active."""

        state = self.view_model.state
        live_can_close = not state.busy and state.primary_action is not LiveAction.STOP
        sweep_can_close = self.sweep_view_model is None or self.sweep_view_model.state.can_close
        calibration_can_close = self.calibration_view_model is None or not self.calibration_view_model.state.busy
        diagnostics_can_close = self.diagnostics_view_model is None or self.diagnostics_view_model.state.can_close
        replay_can_close = self.replay_view_model is None or self.replay_view_model.state.can_close
        tinysa_can_close = self._tinysa is None or self._tinysa.can_close()
        analyzer_can_close = self.analyzer_presenter is None or self.analyzer_presenter.can_close()
        return live_can_close and sweep_can_close and calibration_can_close and diagnostics_can_close and replay_can_close and tinysa_can_close and analyzer_can_close

    def memory_snapshot(self, workspace=None):
        """On-demand scalar diagnostic; creates no page and issues no RX call."""
        from .state.memory_inventory import presentation_memory_snapshot
        return presentation_memory_snapshot(self, workspace)

    def request_shutdown(self) -> CloseState:
        assert self.close_lifecycle is not None
        if self.close_lifecycle.state.phase == "idle" and not self.can_close():
            return CloseState("failed", "Stop must acknowledge before application close")
        state = self.close_lifecycle.request()
        if state.phase == "complete":
            self._release_terminal_presentation()
            self._is_shutdown = True
        return state

    def poll_shutdown(self) -> CloseState:
        assert self.close_lifecycle is not None
        state = self.close_lifecycle.poll()
        if state.phase == "complete":
            self._release_terminal_presentation()
            self._is_shutdown = True
        return state

    def _release_terminal_presentation(self) -> None:
        """GUI-only finalization after ALL composition owners acknowledged.

        Optional presenters without V2 caches retain their existing lifecycle.
        No backend snapshot is rewritten and no data is cleared on failed close.
        """
        if self._terminal_presentation_released:
            return
        for presenter in (self._presenter, self.analyzer_presenter):
            # Only declared hooks; do not invent a port on dynamic mocks/adapters.
            if callable(getattr(type(presenter), "release_presentation_after_shutdown", None)):
                getattr(presenter, "release_presentation_after_shutdown")()
        if self.spectrum_projector is not None:
            self.spectrum_projector.release_presentation_after_shutdown()
        self.view_model.release_presentation_after_shutdown()
        if self.analyzer_view_model is not None:
            self.analyzer_view_model.release_presentation_after_shutdown()
        self._terminal_presentation_released = True

    def _prepare_async_shutdown(self) -> tuple[tuple[str, Callable[[], None]], ...]:
        """GUI-only phase; no device operations, waits or deferred factories."""
        tasks: list[tuple[str, Callable[[], None]]] = []
        for name, presenter in (("sweep-reservation", self._sweep_presenter),
                                ("analyzer", self.analyzer_presenter),
                                ("live", self._presenter),
                                ("calibration", self._calibration_presenter)):
            if presenter is not None:
                prepare = getattr(presenter, "prepare_shutdown", None)
                finish = getattr(presenter, "finish_shutdown", None)
                if not callable(prepare) or not callable(finish):
                    raise TypeError(f"{name} has no split shutdown contract")
                prepare()
                tasks.append((name, finish))
        for name, model in (("diagnostics", self.diagnostics_view_model),
                            ("replay", self.replay_view_model),
                            ("tinysa-activation", None if self._tinysa is None else self._tinysa.activation_view_model),
                            ("tinysa-analyzer", None if self._tinysa is None else self._tinysa.analyzer_view_model)):
            if model is not None:
                finish = model.prepare_shutdown()
                if finish is not None:
                    tasks.append((name, finish))
        if self._unsubscribe_projection is not None:
            self._unsubscribe_projection()
        self._disconnect_projection_delivery()
        if self.spectrum_projector is not None:
            self.spectrum_projector.dispose()
        for bound_model in (self.analyzer_view_model, self.view_model, self.sweep_view_model,
                      self.calibration_view_model):
            if bound_model is not None:
                bound_model.dispose()
        self._presentation_disposed = True
        return tuple(tasks)

    def shutdown(self) -> None:
        """Release presentation subscriptions, then invoke the presenter's existing shutdown."""

        if self._is_shutdown:
            return
        if self.close_lifecycle is not None:
            state = self.request_shutdown()
            if state.phase != "complete":
                raise RuntimeError("asynchronous shutdown requires terminal acknowledgement")
            self._is_shutdown = True
            return
        errors: list[Exception] = []

        def attempt(operation) -> None:
            try:
                operation()
            except Exception as error:  # Every independent owner still gets cleanup.
                errors.append(error)

        # Disconnect presentation callbacks once; these methods own no work
        # and repeating a successful disconnect is not a lifecycle retry.
        if not self._presentation_disposed:
            if self._unsubscribe_projection is not None:
                attempt(self._unsubscribe_projection)
            attempt(self._disconnect_projection_delivery)
            if self.spectrum_projector is not None:
                attempt(self.spectrum_projector.dispose)
            if self.analyzer_view_model is not None:
                attempt(self.analyzer_view_model.dispose)
            attempt(self.view_model.dispose)
            if self.sweep_view_model is not None:
                attempt(self.sweep_view_model.dispose)
            if self.calibration_view_model is not None:
                attempt(self.calibration_view_model.dispose)
            if self.diagnostics_view_model is not None:
                attempt(self.diagnostics_view_model.dispose)
            if self.replay_view_model is not None:
                attempt(self.replay_view_model.dispose)
            self._presentation_disposed = True

        # The retained plan/result Sweep holds a bounded shared reservation.
        # It must cancel/join/release that reservation before Analyzer/Live try
        # to stop the common receiver lifecycle. One failure must never skip a
        # different owner, so all shutdown ports are attempted exactly once.
        if self._sweep_presenter is not None:
            attempt(self._sweep_presenter.shutdown)
        if self.analyzer_presenter is not None:
            attempt(self.analyzer_presenter.shutdown)
        attempt(self._presenter.shutdown)
        if self._calibration_presenter is not None:
            attempt(self._calibration_presenter.shutdown)
        if self._tinysa is not None:
            attempt(self._tinysa.shutdown)

        if errors:
            # Do not make a partial cleanup look terminal; idempotent owners may
            # be retried by an explicit application shutdown path.
            raise errors[0]
        self._release_terminal_presentation()
        self._is_shutdown = True


def compose_v2_live_product(
    presenter: LivePresenterLifecyclePort,
    *,
    sweep_presenter: SweepPresenterLifecyclePort | None = None,
    analyzer_presenter: AnalyzerPresenterLifecyclePort | None = None,
    projection_submit: Callable[[Callable[[], SpectrumProjection]], Future] | None = None,
    persistence_submit: Callable[[Callable[[], object]], Future] | None = None,
    resumable_persistence: bool = False,
    persistence_max_chunks: int = 8,
    allocation_budget: PresentationAllocationBudget | None = None,
    async_shutdown: bool = False,
    calibration_presenter: CalibrationPresenterLifecyclePort | None = None,
    diagnostics_presenter_factory: DiagnosticsPresenterFactory | None = None,
    replay_presenter_factory: ReplayPresenterFactory | None = None,
    tinysa_activation_presenter_factory: TinySaSourceActivationPresenterFactory | None = None,
    tinysa_analyzer_binding_factory: TinySaAnalyzerBindingFactory | None = None,
    now_ns: Callable[[], int] = time_ns,
) -> V2LiveProductComposition:
    """Build V2 replacements without discovery, configuration, RX or an implicit Sweep plan."""

    return V2LiveProductComposition(
        presenter,
        sweep_presenter=sweep_presenter,
        analyzer_presenter=analyzer_presenter,
        projection_submit=projection_submit,
        persistence_submit=persistence_submit,
        resumable_persistence=resumable_persistence,
        persistence_max_chunks=persistence_max_chunks,
        allocation_budget=allocation_budget,
        async_shutdown=async_shutdown,
        calibration_presenter=calibration_presenter,
        diagnostics_presenter_factory=diagnostics_presenter_factory,
        replay_presenter_factory=replay_presenter_factory,
        tinysa_activation_presenter_factory=tinysa_activation_presenter_factory,
        tinysa_analyzer_binding_factory=tinysa_analyzer_binding_factory,
        now_ns=now_ns,
    )
