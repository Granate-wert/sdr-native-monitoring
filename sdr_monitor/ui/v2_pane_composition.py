"""Compose an APP-07 pane plan from selected, independent V2 service graphs.

No discovery, source selection, device open or RX Start occurs here. The
caller must run those explicit operations on control workers before building
this session. Every owner below is the established family application owner;
there is no alternate SDK path or extra serial/IIO/HackRF opener.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import Ad936xPairedSweepPaneProfile, CaptureMeasurementMode, PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverEndpoint, SpectrumTraceEndpoint
from sdr_monitor.services.ad936x_pane_owner import Ad936xPaneOwner
from sdr_monitor.services.ad936x_paired_pane_owner import Ad936xPairedPaneOwner
from sdr_monitor.services.ad936x_paired_sweep_pane_owner import Ad936xPairedSweepPaneOwner
from sdr_monitor.services.hackrf_pane_owner import HackrfPaneOwner
from sdr_monitor.services.rtl_rtbw_pane_owner import RtlRtbwPaneOwner
from sdr_monitor.services.pane_resource_session import PaneCaptureOwner, PaneResourceError, PaneResourceSession
from sdr_monitor.services.parallel_receiver_identity import validate_parallel_receiver_identity
from sdr_monitor.services.pluto_pane_route_admission import PlutoPaneRouteAdmission
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.tinysa_trace_pane_owner import TinySaTracePaneOwner

from .v2_application_graph import V2AnalyzerApplicationGraph


def compose_v2_pane_resource_session(
    layout: PaneLayout,
    groups: tuple[AcquisitionGroup, ...],
    graphs: Mapping[str, V2AnalyzerApplicationGraph],
    leases: ReceiverLeaseManager,
    *, expected_selections: Mapping[str, AnalyzerSourceSelection] | None = None,
    route_admissions: Mapping[str, PlutoPaneRouteAdmission] | None = None,
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
    routes = dict(route_admissions or {})
    if not routes.keys() <= expected or any(not isinstance(value, PlutoPaneRouteAdmission) for value in routes.values()):
        raise PaneResourceError("explicit route admissions must belong to this exact resource plan")
    graph_values = tuple(graphs.values())
    if any(not isinstance(graph, V2AnalyzerApplicationGraph) for graph in graph_values):
        raise PaneResourceError("pane resource needs the current V2 application graph")
    for owner_attribute in (lambda graph: graph, lambda graph: graph.live,
                            lambda graph: graph.analyzer, lambda graph: graph.sources,
                            lambda graph: graph.services.live_sdr, lambda graph: graph.catalog):
        values = tuple(owner_attribute(graph) for graph in graph_values)
        if any(value is None for value in values) or len({id(value) for value in values}) != len(values):
            raise PaneResourceError("parallel panes cannot share one application or catalog owner")
    for name in ("analyzer_hackrf", "analyzer_rtl", "analyzer_hackrf_sweep", "analyzer_tinysa", "recording"):
        owners_for_family = tuple(value for graph in graph_values
                                  if (value := getattr(graph.services, name, None)) is not None)
        if len({id(value) for value in owners_for_family}) != len(owners_for_family):
            raise PaneResourceError("parallel panes cannot share one family or recording owner")

    owners: dict[str, PaneCaptureOwner] = {}
    owner_factories: dict[str, Callable[[], PaneCaptureOwner]] = {}
    identities: dict[str, str | None] = {}
    families: dict[str, DeviceFamily] = {}
    usb_connections = {}
    selected_by_resource = {}
    selections_by_resource = {}
    if expected_selections is not None and set(expected_selections) != expected:
        raise PaneResourceError("staged source selection snapshots differ from plan resources")
    for group in groups:
        resource_id = group.physical_stream_resource_id
        graph = graphs[resource_id]
        selection = graph.live.current_source_selection()
        selected = None if selection is None else selection.selected
        captured = None if expected_selections is None else expected_selections.get(resource_id)
        if (selection is None or selected is None or selection.release_pending
                or graph.sources is None or graph.sources.current() is not selection
                or (expected_selections is not None and
                    (selection is not captured or selection.revision != captured.revision))
                or any(endpoint.source_id != selected.device_id for endpoint in group.endpoints)):
            raise PaneResourceError("pane source lacks one current staged selection")
        selected_by_resource[resource_id] = selected
        route_admission = routes.get(resource_id)
        if route_admission is not None:
            if route_admission.selection is not selection or selected.family is not DeviceFamily.AD936X:
                raise PaneResourceError("explicit route admission belongs to another selection")
            route_admission.validate()
        selections_by_resource[resource_id] = selection
        identities[selected.device_id] = selected.binding.identity_key
        families[selected.device_id] = selected.family
        usb_connections[selected.device_id] = selected.usb_connection
    try:
        validate_parallel_receiver_identity(set(identities), identities, families, usb_connections)
    except ValueError as error:
        raise PaneResourceError(str(error)) from None
    for group in groups:
        resource_id = group.physical_stream_resource_id
        graph = graphs[resource_id]
        selected = selected_by_resource[resource_id]
        selection = selections_by_resource[resource_id]
        endpoint = group.endpoints[0]
        if selected.family is DeviceFamily.AD936X and len(group.endpoints) == 2:
            if any(not isinstance(item, ReceiverEndpoint) for item in group.endpoints):
                raise PaneResourceError("paired AD936x requires two digital RX endpoints")
            first, second = group.endpoints
            assert isinstance(first, ReceiverEndpoint) and isinstance(second, ReceiverEndpoint)
            resource = next(item for item in layout.schedule.resources
                            if item.physical_stream_resource_id == resource_id)
            if all(isinstance(job.profile, Ad936xPairedSweepPaneProfile) for job in resource.jobs):
                owner_factories[resource_id] = partial(Ad936xPairedSweepPaneOwner,
                    graph.live, physical_stream_resource_id=resource_id,
                    source_id=selected.device_id, endpoints=(first, second), route_admission=routes.get(resource_id))
            elif all(job.profile.measurement_mode is CaptureMeasurementMode.RTBW for job in resource.jobs):
                owner_factories[resource_id] = partial(Ad936xPairedPaneOwner,
                    graph.live, physical_stream_resource_id=resource_id,
                    source_id=selected.device_id, endpoints=(first, second),
                    expected_selection=selection, expected_snapshot=graph.live.current_snapshot(),
                    route_admission=routes.get(resource_id))
            else:
                raise PaneResourceError("paired AD936x requires one coherent RTBW or typed paired Sweep mode")
        elif len(group.endpoints) != 1:
            raise PaneResourceError("selected family has no paired acquisition owner")
        elif selected.family is DeviceFamily.AD936X and isinstance(endpoint, ReceiverEndpoint):
            owner_factories[resource_id] = partial(Ad936xPaneOwner,
                graph.live, graph.sweep_router, physical_stream_resource_id=resource_id,
                source_id=selected.device_id, receiver_endpoint_id=endpoint.endpoint_id,
                route_admission=routes.get(resource_id))
        elif selected.family is DeviceFamily.HACKRF and isinstance(endpoint, ReceiverEndpoint):
            if graph.services.analyzer_hackrf is None:
                raise PaneResourceError("selected HackRF graph has no common native RX owner")
            owner_factories[resource_id] = partial(HackrfPaneOwner,
                graph.live, graph.sweep_router, physical_stream_resource_id=resource_id,
                sweep_available=getattr(graph.services, "analyzer_hackrf_sweep", None) is not None,
                source_id=selected.device_id, receiver_endpoint_id=endpoint.endpoint_id)
        elif selected.family is DeviceFamily.RTL_SDR and isinstance(endpoint, ReceiverEndpoint):
            if (graph.services.analyzer_rtl is None or selected.binding.rtl_session_route is None):
                raise PaneResourceError("selected RTL graph has no same-session native RTBW owner")
            owner_factories[resource_id] = partial(RtlRtbwPaneOwner,
                graph.live, physical_stream_resource_id=resource_id,
                source_id=selected.device_id, receiver_endpoint_id=endpoint.endpoint_id)
        elif selected.family is DeviceFamily.TINYSA and isinstance(endpoint, SpectrumTraceEndpoint):
            instrument = graph.services.analyzer_tinysa
            if instrument is None:
                raise PaneResourceError("selected tinySA graph has no common serial owner")
            owner_factories[resource_id] = partial(TinySaTracePaneOwner,
                graph.live, instrument, physical_stream_resource_id=resource_id,
                source_id=selected.device_id, trace_endpoint_id=endpoint.endpoint_id)
        else:
            raise PaneResourceError("pane endpoint does not match a qualified selected source family")
        owners[resource_id] = owner_factories[resource_id]()
    return PaneResourceSession(layout.schedule, groups, owners, leases,
                               source_identity_keys=identities, source_families=families,
                               source_usb_connections=usb_connections,
                               owner_factories=owner_factories)


__all__ = ["compose_v2_pane_resource_session"]
