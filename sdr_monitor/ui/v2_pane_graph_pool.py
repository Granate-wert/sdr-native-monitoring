"""Source-keyed V2 application graph ownership for an independent pane plan.

Construction is inert. Each explicit Stage creates a distinct current V2
service graph, runs explicit local (or opt-in network) discovery and selects one exact source on
the caller's control worker. No RX is started here. A source assigned to
multiple panes must reuse ONE resource/graph in the compiled plan; two
operational routes are never accepted as proof of distinct physical devices.
"""

from __future__ import annotations

from collections.abc import Callable

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.pane_scheduler import PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

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
              include_network: bool = False) -> AnalyzerSourceChoice:
        """Explicit Discover→Select on a new independent graph.

        Network discovery is opt-in per selected source.  It may be slow and
        remains on the caller's off-Qt control worker; no hidden retry occurs.
        """
        if type(include_network) is not bool:
            raise ValueError("network discovery intent must be explicit")
        if (not isinstance(resource_id, str) or not resource_id.strip()
                or not isinstance(source_id, str) or not source_id.strip()):
            raise ValueError("pane resource and source identity are required")
        if (self._closed or self._session is not None or resource_id in self._graphs
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
            if not any(isinstance(choice, AnalyzerSourceChoice) and choice.device_id == source_id
                       for choice in choices):
                raise PaneGraphPoolError("pane source is absent from current local discovery")
            graph.live.select_device(source_id)
            selection = graph.live.current_source_selection()
            selected = None if selection is None else selection.selected
            if (selection is None or selected is None or selected.device_id != source_id
                    or selection.release_pending
                    or graph.sources.current() is not selection):
                raise PaneGraphPoolError("pane source selection lacks a current binding")
            if any(
                (prior := retained.live.current_source_selection()) is not None
                and prior.selected is not None
                and prior.selected.family is selected.family
                and (prior.selected.binding.identity_key is None or selected.binding.identity_key is None)
                for retained in self._graphs.values()
            ):
                raise PaneGraphPoolError("same-family receivers require distinct observed identities")
            if any(
                (prior := retained.live.current_source_selection()) is not None
                and prior.selected is not None
                and selected.binding.identity_key is not None
                and prior.selected.binding.identity_key == selected.binding.identity_key
                for retained in self._graphs.values()
            ):
                raise PaneGraphPoolError("two pane resources alias one physical receiver")
        except Exception:
            self._close_unstaged(resource_id, graph)
            raise PaneGraphPoolError("pane source discovery or selection did not confirm") from None
        self._graphs[resource_id] = graph
        self._source_ids[resource_id] = source_id
        return selected

    def compose(self, layout: PaneLayout, groups: tuple[AcquisitionGroup, ...],
                leases: ReceiverLeaseManager) -> PaneResourceSession | None:
        """Bind an exact compiled plan; preview/apply/Start remain separate."""
        if self._closed or self._session is not None or self._cleanup_pending:
            raise PaneGraphPoolError("pane graph pool is closed, composed or awaiting explicit cleanup")
        expected = (set() if layout.schedule is None else
                    {item.physical_stream_resource_id for item in layout.schedule.resources})
        if expected != set(self._graphs):
            raise PaneGraphPoolError("pane plan differs from the explicitly staged sources")
        session = compose_v2_pane_resource_session(layout, groups, self._graphs, leases)
        self._session = session
        return session

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
                self._source_ids.pop(resource_id, None)
                self._cleanup_pending.pop(resource_id, None)
        if failures:
            raise PaneGraphPoolError("pane application graph close did not confirm for every resource")
        self._session = None
        self._closed = True

    def _close_unstaged(self, resource_id: str, graph: V2AnalyzerApplicationGraph) -> None:
        try:
            graph.live.shutdown()
        except Exception:
            self._cleanup_pending[resource_id] = graph


__all__ = ["PaneGraphPoolError", "PaneProductGraphPool"]
