"""Explicit UI V2 product composition over externally owned public presenters."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from time import time_ns
from typing import Protocol
from weakref import ReferenceType, ref

from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle

from .shell.close_lifecycle import CloseLifecycle, CloseState
from .shell.contracts import ClosePort, V2ShellContext
from .shell.placeholders import default_workspace_definitions
from .spectrum.allocation_budget import PresentationAllocationBudget
from .spectrum.projection import SpectrumProjection, SpectrumProjector
from .state.live_view_state import LiveAction
from .view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel, AnalyzerViewState, SweepPresentationPort
from .view_models.calibration_view_model import CalibrationProfilePresenterPort, CalibrationProfileViewModel
from .view_models.diagnostics_view_model import (
    DeferredDiagnosticsViewModel,
    DiagnosticsPresenterFactory,
)
from .view_models.live_view_model import LivePresenterPort, LiveViewModel
from .view_models.live_calibration_view_model import LiveCalibrationPresenterPort, LiveCalibrationViewModel
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
from .workspaces.analyzer import AnalyzerWorkspaceV2, analyzer_workspace_definition


class LivePresenterLifecyclePort(LivePresenterPort, Protocol):
    """The existing public presenter plus its explicit application shutdown hook."""

    def shutdown(self) -> None: ...
    def rtl_candidate_stage_available(self, source_id: str, selection_revision: int) -> bool: ...


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
        hackrf_sweep_composed: bool = False,
        projection_submit: Callable[[Callable[[], SpectrumProjection]], Future] | None = None,
        persistence_submit: Callable[[Callable[[], object]], Future] | None = None,
        allocation_budget: PresentationAllocationBudget | None = None,
        async_shutdown: bool = False,
        calibration_presenter: CalibrationPresenterLifecyclePort | None = None,
        live_calibration_presenter: LiveCalibrationPresenterPort | None = None,
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
            persistence_submit=persistence_submit))
        self._projection_submit = projection_submit
        self._persistence_submit = persistence_submit
        self._pane_projectors = ([] if self.spectrum_projector is None else [self.spectrum_projector])
        self._projection_activity: dict[SpectrumProjector, bool] = {}
        self._projection_activity_slots: dict[SpectrumProjector, Callable[[bool], None]] = {}
        self._calibration_presenter = calibration_presenter
        self.live_calibration_view_model = (None if live_calibration_presenter is None else
                                            LiveCalibrationViewModel(live_calibration_presenter))
        self._calibration_projector = (None if live_calibration_presenter is None or projection_submit is None else
                                      SpectrumProjector(projection_submit, allocation_budget=self.allocation_budget))
        self._calibration_mode: AnalyzerMode | None = None
        self.view_model = LiveViewModel(presenter, now_ns=now_ns)
        self.analyzer_view_model = (
            AnalyzerViewModel(self.view_model, analyzer_presenter,
                              hackrf_sweep_composed=hackrf_sweep_composed)
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
        self._sweep_projection_backpressure = getattr(analyzer_presenter, "set_projection_in_flight", None)
        self._sweep_preparation_signal = (
            getattr(analyzer_presenter, "poll_preparation_active_changed", None)
            if self.spectrum_projector is not None else None)
        if self._sweep_preparation_signal is not None and self.spectrum_projector is not None:
            self._sweep_preparation_signal.connect(self.spectrum_projector.set_preparation_in_flight)
        if self.spectrum_projector is not None:
            self._connect_projection_activity(self.spectrum_projector)
        if self._calibration_projector is not None:
            self._connect_projection_activity(self._calibration_projector)
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
        self._pane_handle: PaneProductSessionHandle | None = None
        self._analyzer_workspace_ref: ReferenceType[AnalyzerWorkspaceV2] | None = None
        self._terminal_presentation_released = False
        self.close_lifecycle = CloseLifecycle(self._prepare_async_shutdown) if async_shutdown else None
        self._presentation_disposed = False
        live_definition = live_workspace_definition(self.view_model)
        sweep_definition = None if self.sweep_view_model is None else sweep_workspace_definition(self.sweep_view_model)
        calibration_definition = (
            None
            if self.calibration_view_model is None
            else calibration_profiles_workspace_definition(self.calibration_view_model,
                                                            live_calibration=self.live_calibration_view_model)
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
            # The context/workspace may outlive normal close while Qt wrapper
            # deletion is pending. Its factory must not root the composition.
            owner_ref = ref(self)
            def shared_projector_factory() -> SpectrumProjector:
                owner = owner_ref()
                if owner is None:
                    raise RuntimeError("Analyzer projection owner was released")
                return owner.create_shared_pane_projector()
            def remember_analyzer_workspace(widget: AnalyzerWorkspaceV2) -> None:
                owner = owner_ref()
                if owner is not None:
                    owner._analyzer_workspace_ref = ref(widget)
                    widget.enable_independent_pane_setup(
                        install=owner.install_independent_pane_session,
                        uninstall=owner.uninstall_independent_pane_session,
                        rtl_candidate_stage_available=getattr(
                            owner._presenter, "rtl_candidate_stage_available",
                            lambda _source, _revision: False))
                    if owner._pane_handle is not None:
                        widget.install_independent_pane_session(owner._pane_handle)
            workspaces = (analyzer_workspace_definition(self.analyzer_view_model, self.spectrum_projector,
                self.calibration_view_model, shared_projector_factory=shared_projector_factory,
                live_calibration=self.live_calibration_view_model, calibration_projector=self._calibration_projector,
                on_created=remember_analyzer_workspace),) + tuple(
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
                    cancel_pending_control=self.cancel_pending_rf_control,
                ),
            ),
            automatic_discovery_enabled=False,
        )

    def _on_projection_control(self, state: AnalyzerViewState) -> None:
        if self._calibration_mode is not None and self._calibration_mode != state.mode:
            if self.live_calibration_view_model is not None:
                self.live_calibration_view_model.close_binding()
        self._calibration_mode = state.mode
        for projector in self._pane_projectors:
            projector.set_suspended(state.live.busy or state.starting or state.stopping)

    def _commit_live_projection(self, _state: object) -> None:
        for projector in self._pane_projectors:
            projector.request_commit()

    def _connect_projection_activity(self, projector: SpectrumProjector) -> None:
        self._projection_activity[projector] = False
        def slot(active: bool, port: SpectrumProjector = projector) -> None:
            self._projection_activity_changed(port, active)
        self._projection_activity_slots[projector] = slot
        projector.work_active_changed.connect(slot)

    def _projection_activity_changed(self, projector: SpectrumProjector, active: bool) -> None:
        """One idle pane must not release either presenter's shared preparation gate."""
        previous = any(self._projection_activity.values())
        self._projection_activity[projector] = bool(active)
        current = any(self._projection_activity.values())
        if current == previous:
            return
        if callable(self._projection_backpressure):
            self._projection_backpressure(current)
        if callable(self._sweep_projection_backpressure):
            self._sweep_projection_backpressure(current)

    def create_shared_pane_projector(self) -> SpectrumProjector:
        """Give another view of this SAME Analyzer publication an independent lane.

        This does not create a receiver, device owner or source subscription.
        At most four panes can retain projection work. The workspace's single
        model subscription decides which views receive the exact publication.
        """
        if (self._presentation_disposed or self._is_shutdown or self._projection_submit is None
                or self.analyzer_view_model is None):
            raise RuntimeError("shared pane projection is unavailable")
        if len(self._pane_projectors) >= 4:
            raise RuntimeError("at most four Analyzer pane projections are supported")
        projector = SpectrumProjector(
            self._projection_submit, allocation_budget=self.allocation_budget,
            persistence_submit=self._persistence_submit,
        )
        self._pane_projectors.append(projector)
        if self._live_preparation_signal is not None:
            self._live_preparation_signal.connect(projector.set_live_preparation_in_flight)
        if self._sweep_preparation_signal is not None:
            self._sweep_preparation_signal.connect(projector.set_preparation_in_flight)
        self._connect_projection_activity(projector)
        self._on_projection_control(self.analyzer_view_model.state)
        return projector

    def _disconnect_projection_delivery(self) -> None:
        if self._live_preparation_signal is not None:
            for projector in self._pane_projectors:
                self._live_preparation_signal.disconnect(projector.set_live_preparation_in_flight)
            self._live_preparation_signal = None
        if self._sweep_preparation_signal is not None:
            for projector in self._pane_projectors:
                self._sweep_preparation_signal.disconnect(projector.set_preparation_in_flight)
            self._sweep_preparation_signal = None
        if self._projection_delivery_signal is not None:
            self._projection_delivery_signal.disconnect(self._commit_live_projection)
            self._projection_delivery_signal = None
        for projector, slot in self._projection_activity_slots.items():
            projector.work_active_changed.disconnect(slot)
        self._projection_activity_slots.clear()
        self._projection_activity.clear()
        self._projection_backpressure = None
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
        rf = None if self.analyzer_view_model is None else self.analyzer_view_model.rf_controller
        rf_can_close = rf is None or not (rf.pending or rf.fault)
        panes_can_close = self._pane_handle is None or self._pane_handle.can_close()
        widget = None if self._analyzer_workspace_ref is None else self._analyzer_workspace_ref()
        pane_setup_can_close = widget is None or widget.independent_setup_can_close
        return (live_can_close and sweep_can_close and calibration_can_close
                and diagnostics_can_close and replay_can_close and tinysa_can_close
                and analyzer_can_close and rf_can_close and panes_can_close and pane_setup_can_close)

    def install_independent_pane_session(self, handle: PaneProductSessionHandle) -> None:
        """Attach an externally previewed/applied plan to this Analyzer tab."""
        if (not isinstance(handle, PaneProductSessionHandle) or not handle.applied
                or self._pane_handle is not None or self._is_shutdown or self._presentation_disposed
                or self.analyzer_view_model is None or not self.can_close()):
            raise RuntimeError("independent pane product session is unavailable")
        widget = None if self._analyzer_workspace_ref is None else self._analyzer_workspace_ref()
        if widget is not None:
            widget.install_independent_pane_session(handle)
        if self.live_calibration_view_model is not None:
            self.live_calibration_view_model.close_binding()
        self._pane_handle = handle

    def uninstall_independent_pane_session(self) -> None:
        handle = self._pane_handle
        if handle is None:
            return
        if not handle.shutdown_complete:
            raise RuntimeError("independent pane owners must close before layout return")
        widget = None if self._analyzer_workspace_ref is None else self._analyzer_workspace_ref()
        if widget is not None:
            widget.uninstall_independent_pane_session()
        self._pane_handle = None

    def memory_snapshot(self, workspace=None):
        """On-demand scalar diagnostic; creates no page and issues no RX call."""
        from .state.memory_inventory import presentation_memory_snapshot
        return presentation_memory_snapshot(self, workspace)

    def request_shutdown(self) -> CloseState:
        assert self.close_lifecycle is not None
        self.cancel_pending_rf_control()
        if self.live_calibration_view_model is not None:
            self.live_calibration_view_model.close_binding()
        if self.close_lifecycle.state.phase == "idle" and not self.can_close():
            return CloseState("failed", "Stop must acknowledge before application close")
        state = self.close_lifecycle.request()
        if state.phase == "complete":
            self._release_terminal_presentation()
            self._is_shutdown = True
        return state

    def cancel_pending_rf_control(self) -> None:
        """Explicit close intent only; no side effects from readiness checks."""
        rf = None if self.analyzer_view_model is None else self.analyzer_view_model.rf_controller
        if rf is not None and rf.pending:
            if rf.phase in {"entry", "preview", "confirm"}:
                rf.cancel()
            else:
                # Stop acknowledges on the ordinary lanes; never wait here.
                rf.request_user_stop()

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
        widget = None if self._analyzer_workspace_ref is None else self._analyzer_workspace_ref()
        if widget is not None and widget.independent_pane_session is not None:
            widget.independent_pane_session.release_presentation_after_shutdown()
        for presenter in (self._presenter, self.analyzer_presenter):
            # Only declared hooks; do not invent a port on dynamic mocks/adapters.
            if callable(getattr(type(presenter), "release_presentation_after_shutdown", None)):
                getattr(presenter, "release_presentation_after_shutdown")()
        for projector in self._pane_projectors:
            projector.release_presentation_after_shutdown()
        if self._calibration_projector is not None:
            self._calibration_projector.release_presentation_after_shutdown()
        self.view_model.release_presentation_after_shutdown()
        if self.analyzer_view_model is not None:
            self.analyzer_view_model.release_presentation_after_shutdown()
        self._terminal_presentation_released = True

    def _prepare_async_shutdown(self) -> tuple[tuple[str, Callable[[], None]], ...]:
        """GUI-only phase; no device operations, waits or deferred factories."""
        tasks: list[tuple[str, Callable[[], None]]] = []
        if self.live_calibration_view_model is not None:
            self.live_calibration_view_model.dispose()
        if self._calibration_projector is not None:
            self._calibration_projector.dispose()
        if self._pane_handle is not None:
            tasks.append(("independent-pane-resources", self._pane_handle.shutdown_after_stop))
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
        for projector in self._pane_projectors:
            projector.dispose()
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
            if self.live_calibration_view_model is not None:
                attempt(self.live_calibration_view_model.dispose)
            if self._calibration_projector is not None:
                attempt(self._calibration_projector.dispose)
            if self._unsubscribe_projection is not None:
                attempt(self._unsubscribe_projection)
            attempt(self._disconnect_projection_delivery)
            for projector in self._pane_projectors:
                attempt(projector.dispose)
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
        if self._pane_handle is not None:
            attempt(self._pane_handle.shutdown_after_stop)
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
    hackrf_sweep_composed: bool = False,
    projection_submit: Callable[[Callable[[], SpectrumProjection]], Future] | None = None,
    persistence_submit: Callable[[Callable[[], object]], Future] | None = None,
    allocation_budget: PresentationAllocationBudget | None = None,
    async_shutdown: bool = False,
    calibration_presenter: CalibrationPresenterLifecyclePort | None = None,
    live_calibration_presenter: LiveCalibrationPresenterPort | None = None,
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
        hackrf_sweep_composed=hackrf_sweep_composed,
        projection_submit=projection_submit,
        persistence_submit=persistence_submit,
        allocation_budget=allocation_budget,
        async_shutdown=async_shutdown,
        calibration_presenter=calibration_presenter,
        live_calibration_presenter=live_calibration_presenter,
        diagnostics_presenter_factory=diagnostics_presenter_factory,
        replay_presenter_factory=replay_presenter_factory,
        tinysa_activation_presenter_factory=tinysa_activation_presenter_factory,
        tinysa_analyzer_binding_factory=tinysa_analyzer_binding_factory,
        now_ns=now_ns,
    )
