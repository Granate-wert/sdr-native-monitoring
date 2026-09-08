"""Explicit UI V2 product composition over externally owned public presenters."""

from __future__ import annotations

from collections.abc import Callable
from time import time_ns
from typing import Protocol

from .shell.contracts import ClosePort, V2ShellContext
from .shell.placeholders import default_workspace_definitions
from .state.live_view_state import LiveAction
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


class LivePresenterLifecyclePort(LivePresenterPort, Protocol):
    """The existing public presenter plus its explicit application shutdown hook."""

    def shutdown(self) -> None: ...


class SweepPresenterLifecyclePort(SweepPresenterPort, Protocol):
    """The frozen Sweep presenter plus its explicit application shutdown hook."""

    def shutdown(self) -> None: ...


class CalibrationPresenterLifecyclePort(CalibrationProfilePresenterPort, Protocol):
    """The no-write profile browser boundary plus explicit application shutdown."""

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
        calibration_presenter: CalibrationPresenterLifecyclePort | None = None,
        diagnostics_presenter_factory: DiagnosticsPresenterFactory | None = None,
        replay_presenter_factory: ReplayPresenterFactory | None = None,
        tinysa_activation_presenter_factory: TinySaSourceActivationPresenterFactory | None = None,
        tinysa_analyzer_binding_factory: TinySaAnalyzerBindingFactory | None = None,
        now_ns: Callable[[], int] = time_ns,
    ) -> None:
        self._presenter = presenter
        self._sweep_presenter = sweep_presenter
        self._calibration_presenter = calibration_presenter
        self.view_model = LiveViewModel(presenter, now_ns=now_ns)
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
        self.context = V2ShellContext(
            workspaces=workspaces,
            close_ports=(
                ClosePort(
                    name="live-presenter",
                    can_close=self.can_close,
                    shutdown=self.shutdown,
                ),
            ),
            automatic_discovery_enabled=False,
        )

    def can_close(self) -> bool:
        """Refuse a silent close while presenter work or a Live stream is active."""

        state = self.view_model.state
        live_can_close = not state.busy and state.primary_action is not LiveAction.STOP
        sweep_can_close = self.sweep_view_model is None or self.sweep_view_model.state.can_close
        calibration_can_close = self.calibration_view_model is None or not self.calibration_view_model.state.busy
        diagnostics_can_close = self.diagnostics_view_model is None or self.diagnostics_view_model.state.can_close
        replay_can_close = self.replay_view_model is None or self.replay_view_model.state.can_close
        tinysa_can_close = self._tinysa is None or self._tinysa.can_close()
        return live_can_close and sweep_can_close and calibration_can_close and diagnostics_can_close and replay_can_close and tinysa_can_close

    def shutdown(self) -> None:
        """Release presentation subscriptions, then invoke the presenter's existing shutdown."""

        if self._is_shutdown:
            return
        self._is_shutdown = True
        self.view_model.dispose()
        if self.sweep_view_model is not None:
            self.sweep_view_model.dispose()
        if self.calibration_view_model is not None:
            self.calibration_view_model.dispose()
        if self.diagnostics_view_model is not None:
            self.diagnostics_view_model.dispose()
        if self.replay_view_model is not None:
            self.replay_view_model.dispose()
        if self._tinysa is not None:
            self._tinysa.shutdown()
        self._presenter.shutdown()
        if self._sweep_presenter is not None:
            self._sweep_presenter.shutdown()
        if self._calibration_presenter is not None:
            self._calibration_presenter.shutdown()


def compose_v2_live_product(
    presenter: LivePresenterLifecyclePort,
    *,
    sweep_presenter: SweepPresenterLifecyclePort | None = None,
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
        calibration_presenter=calibration_presenter,
        diagnostics_presenter_factory=diagnostics_presenter_factory,
        replay_presenter_factory=replay_presenter_factory,
        tinysa_activation_presenter_factory=tinysa_activation_presenter_factory,
        tinysa_analyzer_binding_factory=tinysa_analyzer_binding_factory,
        now_ns=now_ns,
    )
