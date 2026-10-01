"""Exact APP-07 product session around selected graphs and one pane plan.

Preview/Apply/Start/Stop are separate operations. Construction only allocates
host presentation structures. A caller must run source Stage and Apply off
Qt, show the impact preview before Apply, then expose the explicit Start
commands. No constructor performs RF/serial work or silently starts RX.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Future

from sdr_monitor.domain.pane_scheduler import PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup
from sdr_monitor.services.pane_resource_session import PaneResourcePreview, PaneResourceSession

from .v2.spectrum.allocation_budget import PresentationAllocationBudget
from .v2_pane_delivery_queue import PaneFairDeliveryQueue
from .v2_pane_graph_pool import PaneProductGraphPool
from .v2_pane_presentation import PaneDeliveryPreparer
from .v2_pane_runtime import PanePumpPhase, PaneResourcePump
from .v2_pane_rf_plan import PaneRfChangePreview, PaneRfPlanContext, compile_pane_rf_shift
from .v2_pane_user_plan import PaneUserPlanError


class PaneProductSessionHandle:
    """One composed plan; explicit Stop and off-Qt terminal owner shutdown."""

    def __init__(self, pool: PaneProductGraphPool, layout: PaneLayout,
                 groups: tuple[AcquisitionGroup, ...], session: PaneResourceSession, *,
                 allocation_budget: PresentationAllocationBudget | None = None,
                 source_labels: Mapping[str, str] | None = None,
                 rf_context: PaneRfPlanContext | None = None) -> None:
        if (not isinstance(pool, PaneProductGraphPool) or not isinstance(layout, PaneLayout)
                or layout.schedule is None or pool.composed_session is not session
                or session.schedule is not layout.schedule):
            raise ValueError("pane product session requires one exact staged and composed plan")
        self.pool = pool
        self.layout = layout
        self.session = session
        if rf_context is not None and (not isinstance(rf_context, PaneRfPlanContext)
                                       or rf_context.plan.layout is not layout or rf_context.plan.groups != groups):
            raise ValueError("pane RF context differs from the exact staged product plan")
        self.rf_context = rf_context
        exact_sources = {endpoint.source_id for group in groups for endpoint in group.endpoints}
        labels = {} if source_labels is None else dict(source_labels)
        if (not set(labels) <= exact_sources or any(
                not isinstance(value, str) or not value or len(value) > 160
                or any(ord(character) < 32 for character in value)
                for value in labels.values())):
            raise ValueError("pane display labels require selected, bounded source facts")
        self.source_labels = labels
        self.preparer = PaneDeliveryPreparer(layout, groups,
            allocation_budget if allocation_budget is not None else PresentationAllocationBudget())
        self.queue = PaneFairDeliveryQueue(tuple(slot.request.pane_id for slot in layout.slots
                                                 if slot.request is not None))
        self.pump = PaneResourcePump(session, layout, self.preparer, self.queue)
        self._applied = False
        self._shutdown = False
        self._rf_presentation_pending = False

    def set_rf_presentation_pending(self, pending: bool) -> None:
        """Qt preview/receipt gate only; it neither claims nor controls hardware."""
        if type(pending) is not bool or self._shutdown:
            raise RuntimeError("RF presentation gate requires an active product handle")
        self._rf_presentation_pending = pending

    @property
    def applied(self) -> bool:
        return self._applied

    @property
    def shutdown_complete(self) -> bool:
        return self._shutdown

    def preview(self) -> tuple[PaneResourcePreview, ...]:
        return self.session.preview()

    def apply(self) -> tuple[PaneResourcePreview, ...]:
        """Reserve all exact leases; no receiver Start or hidden retune."""
        if self._applied or self._shutdown:
            raise RuntimeError("pane product plan was already applied or shut down")
        preview = self.session.apply()
        self._applied = True
        self.pump.activate()
        return preview

    def can_close(self) -> bool:
        if (self._rf_presentation_pending or self.session.retained_resource_count
                or self.pump.control_pending()):
            return False
        return not self.pump.activated or all(
            item.phase is PanePumpPhase.STOPPED for item in self.pump.snapshot())

    def preview_rf_shift(self, slot_number: int, shift_hz: float) -> Future[PaneRfChangePreview]:
        context = self.rf_context
        if (context is None or self._shutdown or not self._applied
                or type(slot_number) is not int or not 1 <= slot_number <= len(context.drafts)):
            raise PaneUserPlanError("RF shift needs an active user-authored pane plan")
        source_id = context.drafts[slot_number - 1].source_id
        if source_id is None:
            raise PaneUserPlanError("an Empty pane has no receiver range")
        resource_id = next(resource for resource, source in context.plan.resource_sources if source == source_id)

        def build():
            self._validate_rf_context(context)
            return compile_pane_rf_shift(context, slot_number, shift_hz)
        return self.pump.preview_rf_shift(resource_id, build)

    def _validate_rf_context(self, context: PaneRfPlanContext) -> None:
        """Cached selected facts only; no Discover, probe, query or settings."""
        if self.rf_context is not context or self.layout is not context.plan.layout or self._shutdown:
            raise PaneUserPlanError("original RF context changed before control")
        for resource, source in context.plan.resource_sources:
            selection = self.pool.graph_for(resource).live.current_source_selection()
            expected = next(item for item in context.selections if item[0] == source)
            if (selection is None or selection.selected is not expected[1]
                    or selection.revision != expected[2]):
                raise PaneUserPlanError("selected receiver changed before RF control")

    def stop_for_rf_shift(self, preview: PaneRfChangePreview) -> Future[None]:
        if not isinstance(preview, PaneRfChangePreview):
            raise PaneUserPlanError("RF Stop needs its exact impact preview")
        return self.pump.stop_for_rf_shift(preview, lambda: self._validate_rf_context(preview.proposal.expected_context))

    def apply_rf_shift(self, preview: PaneRfChangePreview) -> Future[None]:
        if (not isinstance(preview, PaneRfChangePreview)
                or self.rf_context is not preview.proposal.expected_context or self._shutdown):
            raise PaneUserPlanError("RF impact preview is stale")

        def commit() -> None:
            self.rf_context = preview.proposal.proposed_context
            self.layout = self.rf_context.plan.layout
        return self.pump.apply_rf_shift(preview, commit,
                                       lambda: self._validate_rf_context(preview.proposal.expected_context))

    def shutdown_after_stop(self) -> None:
        """Run off Qt only after explicit Stop has released every lease."""
        if self._shutdown:
            return
        if not self.can_close():
            raise RuntimeError("pane product needs confirmed Stop before shutdown")
        if self.pump.activated:
            self.pump.join_after_stop(5.0)
        self.pool.close()
        self.preparer.clear()
        self._shutdown = True


__all__ = ["PaneProductSessionHandle"]
