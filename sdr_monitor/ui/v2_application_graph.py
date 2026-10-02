"""The one established V2 Analyzer application graph, reusable per RX resource.

Construction is inert: it does not discover, select, open or start a receiver.
Each call receives its own service owners. Reusing one graph for two physical
resources would retain the old single-source exclusion and is not supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any

from sdr_monitor.application.analyzer_rtbw_router import AnalyzerRtbwRouter
from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.application.analyzer_sources import AnalyzerSourceSelectionApplicationService
from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.services.native_continuous_sweep_factory import (
    NativeContinuousSweepPlanFactory, NativeLiveContinuousSweepDisplayService,
)
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog


@dataclass(frozen=True, slots=True)
class V2AnalyzerApplicationGraph:
    """One and only one receiver/analyzer/control owner, without a Qt view."""

    services: Any
    live: LiveSessionApplicationService
    analyzer: AnalyzerSessionApplicationService
    sweep_router: AnalyzerSweepRouter
    sources: AnalyzerSourceSelectionApplicationService | None
    catalog: SourceCapabilityCatalog | None


def build_v2_analyzer_application_graph(services: Any) -> V2AnalyzerApplicationGraph:
    """Use the SAME product routers/recording exclusion for each future pane graph.

    A separately constructed ``SdrApplicationServices`` is required for each
    independent physical resource. Merely calling this function twice with
    the same services does not create independent ownership.
    """
    display = getattr(services, "analyzer_display", None)
    if display is None:
        display = NativeLiveContinuousSweepDisplayService(services.live_sdr)
    start_live = (services.live_sdr.start_admitted
                  if isinstance(services.live_sdr, NativeLiveSessionService)
                  else services.live_sdr.start)
    catalog = getattr(services, "device_catalog", None)
    sources = (AnalyzerSourceSelectionApplicationService(
        catalog, services.live_sdr, control_transaction=lambda: analyzer.idle_control_operation())
        if isinstance(catalog, SourceCapabilityCatalog)
        and isinstance(services.live_sdr, NativeLiveSessionService) else None)
    router = (AnalyzerRtbwRouter(services.live_sdr, sources, getattr(services, "analyzer_hackrf", None),
                                 getattr(services, "analyzer_rtl", None))
              if sources is not None else None)
    sweep_router = AnalyzerSweepRouter(display, sources, getattr(services, "analyzer_tinysa", None),
                                       getattr(services, "analyzer_hackrf_sweep", None))
    analyzer = AnalyzerSessionApplicationService(router or services.live_sdr, sweep_router,
                                                 start_live=router.start if router else start_live)
    live = LiveSessionApplicationService(
        services.live_sdr,
        sweep_preflight=(partial(NativeContinuousSweepPlanFactory.preflight_native_profile,
                                 services.live_sdr._native)
                         if isinstance(services.live_sdr, NativeLiveSessionService)
                         else NativeContinuousSweepPlanFactory.preflight_profile),
        analyzer=analyzer,
        catalog_close=catalog.close if isinstance(catalog, SourceCapabilityCatalog) else None,
        sources=sources,
        rtbw=router,
        family_sweep_preflight=sweep_router.preflight_family,
    )
    return V2AnalyzerApplicationGraph(services, live, analyzer, sweep_router, sources,
                                      catalog if isinstance(catalog, SourceCapabilityCatalog) else None)


__all__ = ["V2AnalyzerApplicationGraph", "build_v2_analyzer_application_graph"]
