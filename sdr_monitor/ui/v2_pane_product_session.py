"""Exact APP-07 product session around selected graphs and one pane plan.

Preview/Apply/Start/Stop are separate operations. Construction only allocates
host presentation structures. A caller must run source Stage and Apply off
Qt, show the impact preview before Apply, then expose the explicit Start
commands. No constructor performs RF/serial work or silently starts RX.
"""

from __future__ import annotations

from sdr_monitor.domain.pane_scheduler import PaneLayout
from sdr_monitor.domain.receiver_topology import AcquisitionGroup
from sdr_monitor.services.pane_resource_session import PaneResourcePreview, PaneResourceSession

from .v2.spectrum.allocation_budget import PresentationAllocationBudget
from .v2_pane_delivery_queue import PaneFairDeliveryQueue
from .v2_pane_graph_pool import PaneProductGraphPool
from .v2_pane_presentation import PaneDeliveryPreparer
from .v2_pane_runtime import PanePumpPhase, PaneResourcePump


class PaneProductSessionHandle:
    """One composed plan; explicit Stop and off-Qt terminal owner shutdown."""

    def __init__(self, pool: PaneProductGraphPool, layout: PaneLayout,
                 groups: tuple[AcquisitionGroup, ...], session: PaneResourceSession, *,
                 allocation_budget: PresentationAllocationBudget | None = None) -> None:
        if (not isinstance(pool, PaneProductGraphPool) or not isinstance(layout, PaneLayout)
                or layout.schedule is None or pool.composed_session is not session
                or session.schedule is not layout.schedule):
            raise ValueError("pane product session requires one exact staged and composed plan")
        self.pool = pool
        self.layout = layout
        self.session = session
        self.preparer = PaneDeliveryPreparer(layout, groups,
            allocation_budget if allocation_budget is not None else PresentationAllocationBudget())
        self.queue = PaneFairDeliveryQueue(tuple(slot.request.pane_id for slot in layout.slots
                                                 if slot.request is not None))
        self.pump = PaneResourcePump(session, layout, self.preparer, self.queue)
        self._applied = False
        self._shutdown = False

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
        if self.session.retained_resource_count:
            return False
        return not self.pump.activated or all(
            item.phase is PanePumpPhase.STOPPED for item in self.pump.snapshot())

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
