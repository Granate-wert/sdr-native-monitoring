"""Compose an APP-07 pane plan from selected, independent V2 service graphs.

No discovery, source selection, device open or RX Start occurs here. The
caller must run those explicit operations on control workers before building
this session. Every owner below is the established family application owner;
there is no alternate SDK path or extra serial/IIO/HackRF opener.
"""

from __future__ import annotations

from collections.abc import Mapping

from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverEndpoint, SpectrumTraceEndpoint
from sdr_monitor.services.ad936x_rtbw_pane_owner import Ad936xRtbwPaneOwner
from sdr_monitor.services.hackrf_pane_owner import HackrfPaneOwner
from sdr_monitor.services.pane_resource_session import PaneCaptureOwner, PaneResourceError, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.tinysa_trace_pane_owner import TinySaTracePaneOwner

from .v2_application_graph import V2AnalyzerApplicationGraph


def compose_v2_pane_resource_session(
    layout: PaneLayout,
    groups: tuple[AcquisitionGroup, ...],
    graphs: Mapping[str, V2AnalyzerApplicationGraph],
    leases: ReceiverLeaseManager,
) -> PaneResourceSession | None:
    """Bind selected sources to one owner/lease per physical resource.

    Different operational IDs can be aliases for one device (for example
    AD936x USB/IP). Same-family parallel sources need distinct observed
    identities. A source lacking that identity is admitted only if its family
    occurs once in the plan. A graph, native port or catalog must not be reused.
    """
    if not isinstance(layout, PaneLayout) or not isinstance(leases, ReceiverLeaseManager):
        raise TypeError("pane product composition needs a compiled layout and receiver lease manager")
    groups = tuple(groups)
    if layout.schedule is None:
        if groups or graphs:
            raise PaneResourceError("all-Empty layout cannot retain receiver resources")
        return None
    expected = {resource.physical_stream_resource_id for resource in layout.schedule.resources}
    if len(groups) != len(expected) or {group.physical_stream_resource_id for group in groups} != expected:
        raise PaneResourceError("pane layout and receiver groups differ")
    if set(graphs) != expected:
        raise PaneResourceError("one selected application graph is required for each resource")
    graph_values = tuple(graphs.values())
    if any(not isinstance(graph, V2AnalyzerApplicationGraph) for graph in graph_values):
        raise PaneResourceError("pane resource needs the current V2 application graph")
    for owner_attribute in (lambda graph: graph, lambda graph: graph.live,
                            lambda graph: graph.analyzer, lambda graph: graph.sources,
                            lambda graph: graph.services.live_sdr, lambda graph: graph.catalog):
        values = tuple(owner_attribute(graph) for graph in graph_values)
        if any(value is None for value in values) or len({id(value) for value in values}) != len(values):
            raise PaneResourceError("parallel panes cannot share one application or catalog owner")
    for name in ("analyzer_hackrf", "analyzer_hackrf_sweep", "analyzer_tinysa", "recording"):
        owners_for_family = tuple(value for graph in graph_values
                                  if (value := getattr(graph.services, name, None)) is not None)
        if len({id(value) for value in owners_for_family}) != len(owners_for_family):
            raise PaneResourceError("parallel panes cannot share one family or recording owner")

    owners: dict[str, PaneCaptureOwner] = {}
    identities: dict[str, str | None] = {}
    families: dict[str, DeviceFamily] = {}
    for group in groups:
        resource_id = group.physical_stream_resource_id
        graph = graphs[resource_id]
        selection = graph.live.current_source_selection()
        selected = None if selection is None else selection.selected
        if (selection is None or selected is None or selection.release_pending
                or graph.sources is None or graph.sources.current() is not selection
                or len(group.endpoints) != 1
                or group.endpoints[0].source_id != selected.device_id):
            raise PaneResourceError("pane source lacks one current selected binding")
        endpoint = group.endpoints[0]
        identities[selected.device_id] = selected.binding.identity_key
        families[selected.device_id] = selected.family
        if selected.family is DeviceFamily.AD936X and isinstance(endpoint, ReceiverEndpoint):
            owners[resource_id] = Ad936xRtbwPaneOwner(
                graph.live, physical_stream_resource_id=resource_id,
                source_id=selected.device_id, receiver_endpoint_id=endpoint.endpoint_id)
        elif selected.family is DeviceFamily.HACKRF and isinstance(endpoint, ReceiverEndpoint):
            if graph.services.analyzer_hackrf is None:
                raise PaneResourceError("selected HackRF graph has no common native RX owner")
            owners[resource_id] = HackrfPaneOwner(
                graph.live, graph.sweep_router, physical_stream_resource_id=resource_id,
                sweep_available=getattr(graph.services, "analyzer_hackrf_sweep", None) is not None,
                source_id=selected.device_id, receiver_endpoint_id=endpoint.endpoint_id)
        elif selected.family is DeviceFamily.TINYSA and isinstance(endpoint, SpectrumTraceEndpoint):
            instrument = graph.services.analyzer_tinysa
            if instrument is None:
                raise PaneResourceError("selected tinySA graph has no common serial owner")
            owners[resource_id] = TinySaTracePaneOwner(
                graph.live, instrument, physical_stream_resource_id=resource_id,
                source_id=selected.device_id, trace_endpoint_id=endpoint.endpoint_id)
        else:
            raise PaneResourceError("pane endpoint does not match a qualified selected source family")
    return PaneResourceSession(layout.schedule, groups, owners, leases,
                               source_identity_keys=identities, source_families=families)


__all__ = ["compose_v2_pane_resource_session"]
