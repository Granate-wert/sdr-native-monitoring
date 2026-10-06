"""Source-keyed V2 application graph ownership for an independent pane plan.

Construction is inert. Each explicit Stage creates a distinct current V2
service graph, runs explicit local (or opt-in network) discovery and selects one exact source on
the caller's control worker. No RX is started here. A source assigned to
multiple panes must reuse ONE resource/graph in the compiled plan; two
operational routes are never accepted as proof of distinct physical devices.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent
from sdr_monitor.domain.pane_scheduler import PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.parallel_receiver_identity import validate_parallel_receiver_identity
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.interfaces import PlutoOperationalRouteOwner, PlutoUsbAliasOwner
from sdr_monitor.services.pluto_pane_route_admission import PlutoPaneRouteAdmission
from sdr_monitor.domain.pluto_connection import PlutoUsbConnectionExpectation
from sdr_monitor.domain.pluto_usb_alias import PlutoUsbAliasWitness

from .v2_application_graph import V2AnalyzerApplicationGraph, build_v2_analyzer_application_graph
from .v2_pane_composition import compose_v2_pane_resource_session


class PaneGraphPoolError(RuntimeError):
    """Fixed product refusal; source/backend exception text is not exposed."""


def _default_graph_factory(_resource_id: str) -> V2AnalyzerApplicationGraph:
    from sdr_monitor.services.sdr_application_services import build_default_sdr_services

    return build_v2_analyzer_application_graph(build_default_sdr_services())


class PaneProductGraphPool:
    """One selected graph per independent physical resource, no implicit RX.

    Discovery/selection/close can touch SDK or serial and MUST be called off
    Qt. Failed source selection retains its graph if shutdown does not
    confirm; an explicit close retry is required. This is not a global SDK
    arbiter or a substitute for the composed session's physical identity gate.
    """

    def __init__(self, graph_factory: Callable[[str], V2AnalyzerApplicationGraph] = _default_graph_factory) -> None:
        if not callable(graph_factory):
            raise TypeError("pane graph factory must be callable")
        self._factory = graph_factory
        self._graphs: dict[str, V2AnalyzerApplicationGraph] = {}
        self._source_ids: dict[str, str] = {}
        self._selections: dict[str, AnalyzerSourceSelection] = {}
        self._route_intents: dict[str, PlutoOperationalRouteIntent] = {}
        self._route_admissions: dict[str, PlutoPaneRouteAdmission] = {}
        self._usb_candidates: dict[str, PlutoUsbConnectionExpectation] = {}
        self._usb_aliases: dict[str, PlutoUsbAliasWitness] = {}
        self._cleanup_pending: dict[str, V2AnalyzerApplicationGraph] = {}
        self._session: PaneResourceSession | None = None
        self._closed = False

    @property
    def staged_resource_ids(self) -> tuple[str, ...]:
        return tuple(self._graphs)

    @property
    def cleanup_pending_resource_ids(self) -> tuple[str, ...]:
        return tuple(self._cleanup_pending)

    @property
    def composed_session(self) -> PaneResourceSession | None:
        return self._session

    def stage(self, resource_id: str, source_id: str, *,
              include_network: bool = False,
              operational_route: PlutoOperationalRouteIntent | None = None) -> AnalyzerSourceChoice:
        """Explicit Discover→Select on a new independent graph.

        Network discovery is opt-in per selected source.  It may be slow and
        remains on the caller's off-Qt control worker; no hidden retry occurs.
        """
        if type(include_network) is not bool:
            raise ValueError("network discovery intent must be explicit")
        if operational_route is not None and not isinstance(operational_route, PlutoOperationalRouteIntent):
            raise TypeError("operational route requires typed explicit intent")
        if (not isinstance(resource_id, str) or not resource_id.strip()
                or not isinstance(source_id, str) or not source_id.strip()):
            raise ValueError("pane resource and source identity are required")
        if (self._closed or self._session is not None or self._cleanup_pending or resource_id in self._graphs
                or resource_id in self._cleanup_pending or source_id in self._source_ids.values()):
            raise PaneGraphPoolError("pane source cannot be staged twice or after plan composition")
        try:
            graph = self._factory(resource_id)
        except Exception:
            raise PaneGraphPoolError("pane application graph construction failed") from None
        if not isinstance(graph, V2AnalyzerApplicationGraph) or graph.catalog is None or graph.sources is None:
            # A malformed factory might have partially constructed owners.
            # Only a valid graph exposes a known shutdown boundary.
            if isinstance(graph, V2AnalyzerApplicationGraph):
                self._close_unstaged(resource_id, graph)
            raise PaneGraphPoolError("pane resource lacks a current source-capable V2 graph")
        for retained in self._graphs.values():
            if (graph is retained or graph.live is retained.live
                    or graph.services.live_sdr is retained.services.live_sdr
                    or graph.catalog is retained.catalog):
                # Never close this attempted graph: it aliases an already
                # retained owner and shutdown would steal that resource.
                raise PaneGraphPoolError("parallel pane resources reused one application owner")
        try:
            choices = (graph.live.discover() if include_network else
                       graph.live.discover(local_only=True))
            discovered = next((choice for choice in choices if isinstance(choice, AnalyzerSourceChoice)
                               and choice.device_id == source_id), None)
            if discovered is None and operational_route is None:
                raise PaneGraphPoolError("pane source is absent from current local discovery")
            if operational_route is None:
                graph.live.select_device(source_id)
            else:
                if discovered is not None and discovered.family is not DeviceFamily.AD936X:
                    raise PaneGraphPoolError("explicit USB/IP route belongs only to AD936x")
                snapshot = graph.live.select_manual_uri(operational_route.uri)
                if (snapshot.device is None or snapshot.device.uri != operational_route.uri
                        or snapshot.device.device_id != source_id or snapshot.error is not None):
                    raise PaneGraphPoolError("explicit route does not retain selected source")
            selection = graph.live.current_source_selection()
            selected = None if selection is None else selection.selected
            if (selection is None or selected is None or selected.device_id != source_id
                    or selection.release_pending
                    or graph.sources.current() is not selection):
                raise PaneGraphPoolError("pane source selection lacks a current binding")
            if operational_route is not None:
                if selected.family is not DeviceFamily.AD936X:
                    raise PaneGraphPoolError("explicit route observation is not AD936x")
                if discovered is None:
                    # Explicitly addressed IP can be absent from broadcast.
                    # Only SAME fresh stable owned identity may match the
                    # caller's logical source; unknown stale USB routes refuse.
                    if not operational_route.uri.startswith("ip:") or selected.binding.identity_key is None:
                        raise PaneGraphPoolError("unadvertised route needs a stable observed AD936x IP identity")
                elif (selected.binding.identity_key != discovered.binding.identity_key
                        or selected.binding.adapter_id != discovered.binding.adapter_id
                        or (discovered.binding.identity_key is None
                            and selected.usb_connection != discovered.usb_connection)):
                    raise PaneGraphPoolError("explicit route changed the freshly discovered identity")
                route_owner = graph.services.live_sdr
                if not isinstance(route_owner, PlutoOperationalRouteOwner):
                    raise PaneGraphPoolError("selected AD936x owner lacks explicit route admission")
                route_owner.bind_operational_route(operational_route, source_id=source_id)
                route_admission = PlutoPaneRouteAdmission(operational_route, selection,
                    graph.live.current_source_selection, route_owner)
                route_admission.validate()
            staged_choices = {prior.selected.device_id: prior.selected
                              for prior in self._selections.values() if prior.selected is not None}
            all_choices = (*staged_choices.values(), selected)
            admissions = dict(self._route_admissions)
            if operational_route is not None:
                admissions[resource_id] = route_admission
            candidates = dict(self._usb_candidates)
            if discovered is not None and discovered.usb_connection is not None:
                candidates[source_id] = discovered.usb_connection
            aliases = self._observe_required_aliases(all_choices, admissions, candidates)
            source_ids = {item.device_id for item in all_choices}
            validate_parallel_receiver_identity(
                source_ids,
                {item.device_id: item.binding.identity_key for item in all_choices},
                {item.device_id: item.family for item in all_choices},
                {item.device_id: item.usb_connection for item in all_choices},
                aliases,
            )
        except Exception:
            self._close_unstaged(resource_id, graph)
            raise PaneGraphPoolError("pane source discovery or selection did not confirm") from None
        self._graphs[resource_id] = graph
        self._source_ids[resource_id] = source_id
        self._selections[resource_id] = selection
        self._route_admissions = admissions
        self._usb_candidates = candidates
        self._usb_aliases = aliases
        if operational_route is not None:
            self._route_intents[resource_id] = operational_route
        return selected

    def _observe_required_aliases(self, choices: tuple[AnalyzerSourceChoice, ...],
                                 admissions: dict[str, PlutoPaneRouteAdmission],
                                 candidates: dict[str, PlutoUsbConnectionExpectation]) -> dict[str, PlutoUsbAliasWitness]:
        """Only explicit known IP + unknown USB needs a fresh separate witness.

        Both staging orders work. Observation uses the retained known owner's
        existing read-only observer; no invented serial or Ethernet USB lease.
        Missing USB evidence still refuses. Not a cross-process hardware lock.
        """
        aliases = dict(self._usb_aliases)
        unknown_ad = any(item.family is DeviceFamily.AD936X and item.binding.identity_key is None
                         for item in choices)
        if not unknown_ad:
            return aliases
        for item in choices:
            if (item.family is not DeviceFamily.AD936X or item.binding.identity_key is None
                    or item.usb_connection is not None):
                continue
            resource = next((key for key, admission in admissions.items()
                             if admission.selection.selected is not None
                             and admission.selection.selected.device_id == item.device_id), None)
            connection = candidates.get(item.device_id)
            if resource is None or connection is None:
                raise PaneGraphPoolError("parallel unknown USB/known IP needs a fresh observed USB alias")
            admission = admissions[resource]
            admission.validate()
            if not isinstance(admission.owner, PlutoUsbAliasOwner):
                raise PaneGraphPoolError("known IP owner cannot admit a USB alias")
            if item.device_id not in aliases:
                witness = admission.owner.observe_operational_usb_alias(connection)
                updated = replace(admission, usb_alias=witness)
                updated.validate()
                admissions[resource] = updated
                aliases[item.device_id] = witness
        return aliases

    def compose(self, layout: PaneLayout, groups: tuple[AcquisitionGroup, ...],
                leases: ReceiverLeaseManager) -> PaneResourceSession | None:
        """Bind an exact compiled plan; preview/apply/Start remain separate."""
        if self._closed or self._session is not None or self._cleanup_pending:
            raise PaneGraphPoolError("pane graph pool is closed, composed or awaiting explicit cleanup")
        expected = (set() if layout.schedule is None else
                    {item.physical_stream_resource_id for item in layout.schedule.resources})
        if expected != set(self._graphs):
            raise PaneGraphPoolError("pane plan differs from the explicitly staged sources")
        for resource_id, captured in self._selections.items():
            graph = self._graphs[resource_id]
            current = graph.live.current_source_selection()
            if (current is not captured or current.revision != captured.revision
                    or graph.sources is None or graph.sources.current() is not captured):
                raise PaneGraphPoolError("pane source selection changed after Stage")
            intent = self._route_intents.get(resource_id)
            if intent is not None:
                try:
                    route_owner = graph.services.live_sdr
                    if not isinstance(route_owner, PlutoOperationalRouteOwner):
                        raise PaneGraphPoolError("selected AD936x owner lacks explicit route admission")
                    route_owner.validate_operational_route(intent, source_id=self._source_ids[resource_id])
                except Exception:
                    raise PaneGraphPoolError("pane explicit route changed after Stage") from None
        self.validate_explicit_routes()
        session = compose_v2_pane_resource_session(layout, groups, self._graphs, leases,
                                                   expected_selections=self._selections,
                                                   route_admissions=self._route_admissions)
        self._session = session
        return session

    def validate_explicit_routes(self, *, refresh_aliases: bool = False) -> None:
        """All pinned resources revalidated before any initial Apply write."""
        if self._usb_aliases:
            for resource, captured in self._selections.items():
                if self._graphs[resource].live.current_source_selection() is not captured:
                    raise LiveAdmissionRejected("parallel source selection changed before Apply")
        for admission in self._route_admissions.values():
            admission.validate()
        if refresh_aliases:
            for admission in self._route_admissions.values():
                admission.refresh_alias()

    def graph_for(self, resource_id: str) -> V2AnalyzerApplicationGraph:
        try:
            return self._graphs[resource_id]
        except KeyError:
            raise ValueError("unknown staged pane resource") from None

    def close(self) -> None:
        """Explicit owner close after every composed resource lease is gone."""
        if self._session is not None and self._session.retained_resource_count:
            raise PaneGraphPoolError("pane receiver Stop must release every resource before graph close")
        if self._session is not None:
            self._session.retire_after_stop()
        failures: list[str] = []
        for resource_id, graph in tuple({**self._graphs, **self._cleanup_pending}.items()):
            try:
                graph.live.shutdown()
            except Exception:
                self._cleanup_pending[resource_id] = graph
                failures.append(resource_id)
            else:
                self._graphs.pop(resource_id, None)
                source_id = self._source_ids.pop(resource_id, None)
                self._selections.pop(resource_id, None)
                self._route_intents.pop(resource_id, None)
                self._route_admissions.pop(resource_id, None)
                if source_id is not None:
                    self._usb_candidates.pop(source_id, None)
                    self._usb_aliases.pop(source_id, None)
                self._cleanup_pending.pop(resource_id, None)
        if failures:
            raise PaneGraphPoolError("pane application graph close did not confirm for every resource")
        self._session = None
        self._usb_candidates.clear()
        self._usb_aliases.clear()
        self._closed = True

    def _close_unstaged(self, resource_id: str, graph: V2AnalyzerApplicationGraph) -> None:
        try:
            graph.live.shutdown()
        except Exception:
            self._cleanup_pending[resource_id] = graph


__all__ = ["PaneGraphPoolError", "PaneProductGraphPool"]
