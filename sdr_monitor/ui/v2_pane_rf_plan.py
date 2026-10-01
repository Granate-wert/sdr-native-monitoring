"""Pure RF shifts on the original UI V2 intent, never on a viewport or grid.

One pane range changes; all other requested ranges, priorities, deadlines,
DSP/RF-filter settings and source selections survive. The normal compiler
rechecks shared/time-sliced feasibility and hardware/memory bounds before any
Stop. Effective transport-specific rounding is explicit, never edge-clamped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import copysign, floor, isfinite

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.services.pane_resource_session import PaneResourcePlanPreview

from .v2_pane_user_plan import PaneSlotDraft, PaneUserPlan, PaneUserPlanError, compile_user_pane_plan


@dataclass(frozen=True, slots=True)
class PaneRfPlanContext:
    plan: PaneUserPlan
    drafts: tuple[PaneSlotDraft, ...]
    selections: tuple[tuple[str, AnalyzerSourceChoice, int], ...]

    def __post_init__(self) -> None:
        try:
            drafts = tuple(self.drafts)
            selections = tuple(tuple(entry) for entry in self.selections)
        except TypeError:
            raise PaneUserPlanError("RF context requires immutable typed draft and selection entries") from None
        if (not isinstance(self.plan, PaneUserPlan)
                or any(not isinstance(draft, PaneSlotDraft) for draft in drafts)
                or any(len(entry) != 3 for entry in selections)):
            raise PaneUserPlanError("RF context requires immutable typed draft and selection entries")
        sources = {draft.source_id for draft in drafts if draft.source_id is not None}
        if (tuple(draft.number for draft in drafts) != tuple(slot.number for slot in self.plan.layout.slots)
                or sources != {source for source, _choice, _revision in selections}
                or len(selections) != len(sources)
                or any(not isinstance(choice, AnalyzerSourceChoice) or choice.device_id != source
                       or type(revision) is not int or revision < 0
                       for source, choice, revision in selections)):
            raise PaneUserPlanError("RF change requires the original draft and exact staged selections")
        object.__setattr__(self, "drafts", drafts)
        object.__setattr__(self, "selections", selections)


@dataclass(frozen=True, slots=True)
class PaneRfShiftProposal:
    expected_context: PaneRfPlanContext
    proposed_context: PaneRfPlanContext
    slot_number: int
    physical_stream_resource_id: str
    requested_shift_hz: float
    effective_shift_hz: float
    quantum_hz: float


@dataclass(frozen=True, slots=True)
class PaneRfChangePreview:
    proposal: PaneRfShiftProposal
    resource: PaneResourcePlanPreview

    def __post_init__(self) -> None:
        if (self.resource.expected_schedule is not self.proposal.expected_context.plan.layout.schedule
                or self.resource.proposed_schedule is not self.proposal.proposed_context.plan.layout.schedule
                or self.resource.physical_stream_resource_id != self.proposal.physical_stream_resource_id):
            raise PaneUserPlanError("RF preview differs from its exact user and resource plans")


def compile_pane_rf_shift(context: PaneRfPlanContext, slot_number: int,
                          shift_hz: float) -> PaneRfShiftProposal:
    """No Discover, claim, RF setter, lease, Stop, Start or GUI allocation."""
    if (not isinstance(context, PaneRfPlanContext) or type(slot_number) is not int
            or not 1 <= slot_number <= len(context.drafts)
            or type(shift_hz) not in {int, float} or not isfinite(shift_hz)):
        raise PaneUserPlanError("RF shift needs a finite offset and occupied original pane")
    draft = context.drafts[slot_number - 1]
    if draft.source_id is None or draft.start_hz is None or draft.stop_hz is None:
        raise PaneUserPlanError("an Empty pane has no receiver range to shift")
    selected = {source: choice for source, choice, _revision in context.selections}
    revisions = {source: revision for source, _choice, revision in context.selections}
    choice = selected[draft.source_id]
    quantum = (1_000_000.0 if choice.family is DeviceFamily.HACKRF
               and draft.measurement_mode is CaptureMeasurementMode.SWEEP else 1.0)
    effective = copysign(floor(abs(shift_hz) / quantum + 0.5) * quantum, shift_hz)
    if effective == 0:
        raise PaneUserPlanError("RF shift is smaller than the admitted range quantum")
    changed = replace(draft, start_hz=draft.start_hz + effective, stop_hz=draft.stop_hz + effective)
    drafts = tuple(changed if item.number == slot_number else item for item in context.drafts)
    plan = compile_user_pane_plan(drafts, selected, revisions)
    if (plan.groups != context.plan.groups or plan.resource_sources != context.plan.resource_sources):
        raise PaneUserPlanError("RF shift cannot change the selected resource topology")
    resource_id = next(resource for resource, source in plan.resource_sources if source == draft.source_id)
    return PaneRfShiftProposal(context, PaneRfPlanContext(plan, drafts, context.selections),
                               slot_number, resource_id, float(shift_hz), effective, quantum)


__all__ = ["PaneRfPlanContext", "PaneRfShiftProposal", "PaneRfChangePreview", "compile_pane_rf_shift"]
