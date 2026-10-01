"""Explicit off-Qt Stage/Preview/Apply for a user-authored APP-07 layout.

The caller owns the returned handle until it is installed into the V2 product
composition or fully shut down.  Stage performs fresh local Discover/Select on
one graph per *unique* source.  Preview never starts RX; Apply stages the first
AD936x configuration and reserves leases, but Start remains a separate action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sdr_monitor.domain.pane_scheduler import PaneRevisitEstimate
from sdr_monitor.services.pane_resource_session import PaneResourcePreview
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from .v2_pane_graph_pool import PaneProductGraphPool
from .v2_pane_product_session import PaneProductSessionHandle
from .v2_pane_rf_plan import PaneRfPlanContext
from .v2_pane_user_plan import PaneSlotDraft, PaneUserPlan, PaneUserPlanError, compile_user_pane_plan


class PaneUserStageError(RuntimeError):
    """Fixed refusal. Retained pool means an explicit cleanup retry is needed."""

    def __init__(self, message: str, pool: PaneProductGraphPool | None = None, *,
                 revisit_violations: tuple[PaneRevisitEstimate, ...] = ()) -> None:
        super().__init__(message)
        self.pool = pool
        self.revisit_violations = revisit_violations


@dataclass(frozen=True, slots=True)
class PreparedPaneUserSession:
    plan: PaneUserPlan
    handle: PaneProductSessionHandle
    preview: tuple[PaneResourcePreview, ...]


def prepare_user_pane_session(
    drafts: tuple[PaneSlotDraft, ...], *,
    pool_factory: Callable[[], PaneProductGraphPool] = PaneProductGraphPool,
) -> PreparedPaneUserSession:
    """Run only on a control worker after explicit user Stage/Preview."""
    pool = pool_factory()
    try:
        source_order = tuple(dict.fromkeys(draft.source_id for draft in drafts if draft.source_id is not None))
        network_intent: dict[str, bool] = {}
        for draft in drafts:
            if draft.source_id is None:
                continue
            if draft.source_id in network_intent and network_intent[draft.source_id] != draft.network_discovery:
                raise PaneUserStageError("one source has conflicting network discovery intent")
            network_intent[draft.source_id] = draft.network_discovery
        selected = {}
        revisions = {}
        for index, source_id in enumerate(source_order, 1):
            assert source_id is not None
            resource_id = f"pane-resource-{index}"
            selected[source_id] = pool.stage(
                resource_id, source_id, include_network=network_intent[source_id])
            selection = pool.graph_for(resource_id).live.current_source_selection()
            if selection is None or selection.selected is not selected[source_id]:
                raise PaneUserStageError("pane source selection changed during Stage")
            revisions[source_id] = selection.revision
        plan = compile_user_pane_plan(drafts, selected, revisions)
        session = pool.compose(plan.layout, plan.groups, ReceiverLeaseManager(max_active_resources=4))
        if session is None:
            raise PaneUserStageError("an occupied pane layout has no resource session")
        handle = PaneProductSessionHandle(pool, plan.layout, plan.groups, session,
                                          source_labels={source: choice.label for source, choice in selected.items()},
                                          rf_context=PaneRfPlanContext(plan, drafts, tuple(
                                              (source, selected[source], revisions[source]) for source in source_order)))
        return PreparedPaneUserSession(plan, handle, handle.preview())
    except Exception as error:
        violations = error.revisit_violations if isinstance(error, PaneUserPlanError) else ()
        try:
            pool.close()
        except Exception:
            raise PaneUserStageError("pane Stage failed and graph cleanup requires an explicit retry", pool,
                                     revisit_violations=violations) from None
        raise PaneUserStageError("pane sources or ranges did not confirm on fresh Stage",
                                 revisit_violations=violations) from None


def apply_user_pane_session(prepared: PreparedPaneUserSession) -> None:
    """Explicit Apply; no RX Start or hidden retry of a failed owner."""
    handle = prepared.handle
    if handle.applied or handle.shutdown_complete:
        raise PaneUserStageError("pane plan was already applied or closed")
    if any(item.recording_conflict for item in prepared.preview):
        raise PaneUserStageError("recording conflicts with one or more proposed receiver plans")
    for resource_id, configuration in prepared.plan.initial_ad_configurations:
        result = handle.pool.graph_for(resource_id).live.apply_configuration(configuration)
        if result.error is not None or result.applied is None:
            raise PaneUserStageError("AD936x initial configuration did not confirm on Apply")
    handle.apply()


def discard_user_pane_session(prepared: PreparedPaneUserSession) -> None:
    """Explicit off-Qt Discard before Apply, or terminal close after Stop."""
    prepared.handle.shutdown_after_stop()


__all__ = [
    "PaneUserStageError", "PreparedPaneUserSession", "prepare_user_pane_session",
    "apply_user_pane_session", "discard_user_pane_session",
]
