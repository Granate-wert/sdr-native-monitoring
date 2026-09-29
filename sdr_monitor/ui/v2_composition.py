"""APP-00 composition root: UI V2 over the current application boundaries.

No Legacy widgets or frozen-worktree services are composed here. An injected
service bundle is useful for tests; production creates exactly one current graph.
"""

from __future__ import annotations


def build_v2_shell(services=None):
    """Compose the V2 Live replacement over the existing service/presenter graph."""

    from ..services import build_default_sdr_services
    from ..application import (
        SweepControlApplicationService,
        CalibrationControlApplicationService, DiagnosticsControlApplicationService,
        ReplayControlApplicationService,
    )
    from ..ui.presenters import CalibrationPresenter, DiagnosticsPresenter, LivePresenter, SweepPresenter
    from ..ui.v2.product_live import compose_v2_live_product
    from ..ui.v2.shell import AppShellV2

    def make_tinysa_activation_presenter():
        """Create the serial-capable source presenter only after V2 Discover."""

        from ..application.tinysa_source_activation import TinySaSourceActivationApplicationService
        from ..services.tinysa_serial_source_backend import TinySaSerialSourceBackend
        from ..services.tinysa_source_composition import TinySaSourceCompositionService
        from ..ui.presenters.tinysa_source_activation_presenter import TinySaSourceActivationPresenter

        return TinySaSourceActivationPresenter(
            TinySaSourceActivationApplicationService(
                TinySaSourceCompositionService(TinySaSerialSourceBackend())
            )
        )

    def make_replay_presenter():
        """Create the file-owning presenter only after V2 Open spectrum recording."""

        from ..ui.presenters.replay_presenter import ReplayPresenter

        return ReplayPresenter(ReplayControlApplicationService(services.replay))

    def make_tinysa_analyzer_binding(composed):
        """Compose one existing analyzer presenter after verified-source Compose only."""

        from ..application.tinysa_analyzer import TinySaAnalyzerApplicationService
        from ..services.tinysa_source_composition import TinySaComposedSource
        from ..ui.presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenter
        from ..ui.v2.view_models.tinysa_view_model import TinySaAnalyzerBinding

        if not isinstance(composed, TinySaComposedSource):
            raise TypeError("tinySA V2 analyzer binding requires a composed source")
        return TinySaAnalyzerBinding(
            source=composed.verified,
            presenter=TinySaAnalyzerPresenter(
                TinySaAnalyzerApplicationService(
                    composed.collector,
                    composed.settings_executor,
                )
            ),
        )

    # One current service graph backs the V2 product shell.
    # Their documented construction opens no device; all discovery,
    # configuration and RX remain explicit presenter commands from Live V2.
    services = build_default_sdr_services() if services is None else services
    from ..application.analyzer_continuous_sweep import AnalyzerContinuousSweepApplicationService
    from ..ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
    from ..ui.v2_application_graph import build_v2_analyzer_application_graph
    from ..ui.v2.state.prepared_sweep import SweepSnapshotPreparer
    from ..ui.v2.state.prepared_live import LiveSnapshotPreparer
    from ..ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
    from ..ui.v2.state.source_admission import PresentationSourceAdmission
    allocation_budget = PresentationAllocationBudget()
    source_admission = PresentationSourceAdmission(allocation_budget)
    graph = build_v2_analyzer_application_graph(services)
    live_application = graph.live
    analyzer = graph.analyzer
    sweep_router = graph.sweep_router
    analyzer_presenter = ContinuousSweepPresenter(
        AnalyzerContinuousSweepApplicationService(live_application, sweep_router),
        snapshot_preparer=SweepSnapshotPreparer(allocation_budget),
        snapshot_admitter=source_admission.sweep,
    )
    live_presenter = LivePresenter(live_application, snapshot_preparer=LiveSnapshotPreparer(allocation_budget),
                                   snapshot_admitter=source_admission.live)
    composition = compose_v2_live_product(
        live_presenter,
        projection_submit=live_presenter.submit_display_task,
        allocation_budget=allocation_budget,
        async_shutdown=True,
        analyzer_presenter=analyzer_presenter,
        hackrf_sweep_composed=getattr(services, "analyzer_hackrf_sweep", None) is not None,
        sweep_presenter=SweepPresenter(SweepControlApplicationService(services.sweep, analyzer=analyzer)),
        calibration_presenter=CalibrationPresenter(CalibrationControlApplicationService(services.calibration)),
        diagnostics_presenter_factory=lambda: DiagnosticsPresenter(DiagnosticsControlApplicationService(services.diagnostics)),
        replay_presenter_factory=make_replay_presenter,
        # Real common instrument graphs cannot expose a second serial owner.
        tinysa_activation_presenter_factory=(None if getattr(services, "analyzer_tinysa", None) is not None else make_tinysa_activation_presenter),
        tinysa_analyzer_binding_factory=(None if getattr(services, "analyzer_tinysa", None) is not None else make_tinysa_analyzer_binding),
    )
    return AppShellV2(context=composition.context)
