"""APP-00 composition root: UI V2 over the current application boundaries.

No Legacy widgets or frozen-worktree services are composed here. An injected
service bundle is useful for tests; production creates exactly one current graph.
"""

from __future__ import annotations


def build_v2_shell(services=None):
    """Compose the V2 Live replacement over the existing service/presenter graph."""

    from ..services import build_default_sdr_services
    from ..application import (
        LiveSessionApplicationService, SweepControlApplicationService,
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
    composition = compose_v2_live_product(
        LivePresenter(LiveSessionApplicationService(services.live_sdr)),
        sweep_presenter=SweepPresenter(SweepControlApplicationService(services.sweep)),
        calibration_presenter=CalibrationPresenter(CalibrationControlApplicationService(services.calibration)),
        diagnostics_presenter_factory=lambda: DiagnosticsPresenter(DiagnosticsControlApplicationService(services.diagnostics)),
        replay_presenter_factory=make_replay_presenter,
        tinysa_activation_presenter_factory=make_tinysa_activation_presenter,
        tinysa_analyzer_binding_factory=make_tinysa_analyzer_binding,
    )
    return AppShellV2(context=composition.context)

