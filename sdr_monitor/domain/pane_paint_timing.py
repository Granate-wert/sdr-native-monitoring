"""Original-view paint boundary evidence and conservative elapsed intervals.

The clock sample immediately AFTER a successful base Qt paint return is not
the exact instant of that return, a DWM presentation, or an RF timestamp.
The true base-return instant is enclosed by before-paint/after-return samples.
Ledger bookkeeping time is a separate boundary and is never substituted.
No offset extrapolation, midpoint percentile or negative-bound clipping.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .analytical_ready import ReadyClockMapping
from .host_clock import HostClockScope
from .pane_delivery_obligation import PaneDeliveryEvent, PaneDeliveryObligationRef, PaneDeliveryStage


@dataclass(frozen=True, slots=True)
class PanePaintReturnReceipt:
    ref: PaneDeliveryObligationRef
    host_clock: HostClockScope
    before_paint_ns: int
    sampled_after_return_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.ref, PaneDeliveryObligationRef) or not isinstance(self.host_clock, HostClockScope):
            raise ValueError("paint return requires exact view ref and typed host clock")
        for stamp in (self.before_paint_ns, self.sampled_after_return_ns):
            if type(stamp) is not int or not 0 <= stamp < (1 << 63):
                raise ValueError("paint clock sample is outside signed nanosecond range")
        if self.sampled_after_return_ns < self.before_paint_ns:
            raise ValueError("paint return host clock regressed")
        if self.host_clock.process_id != self.ref.identity.ready.host_process_id:
            raise ValueError("paint and original ready receipt belong to different processes")


class PaintTimingState(StrEnum):
    BOUNDED = "bounded_measured_host_intervals"
    ORDER_UNCERTAIN = "overlapping_ready_and_paint_intervals"
    MISSING_PAINT_RECEIPT = "missing_original_paint_return_receipt"
    UNMAPPED_READY = "ready_outside_measured_clock_probes"
    UNKNOWN_HOST_CLOCK = "unknown_host_clock_origin"
    FOREIGN_HOST_CLOCK = "incompatible_host_clock_origin"
    INVALID_BOUNDARY = "invalid_original_paint_boundary"
    CLOCK_ORDER_INVALID = "host_boundary_order_invalid"


@dataclass(frozen=True, slots=True)
class ReadyToPaintTiming:
    state: PaintTimingState
    # Signed uncertainty intervals. A negative lower bound is uncertainty,
    # NOT a measured negative latency; never replace it with zero.
    base_return_elapsed_ns: tuple[int, int] | None = None
    after_return_sample_elapsed_ns: tuple[int, int] | None = None
    # Only available when bookkeeping uses the same declared host clock.
    bookkeeping_after_sample_ns: int | None = None


def evaluate_ready_to_paint(event: PaneDeliveryEvent) -> ReadyToPaintTiming:
    """Evaluate one retained original-view event; no clock/SDK/Qt calls.

    No lifetime completeness or percentile verdict follows from this single
    finite-ledger event. Coverage/eviction/supersession must be reported by a
    measurement harness alongside any later interval statistics.
    """
    if (not isinstance(event, PaneDeliveryEvent) or not isinstance(event.ref, PaneDeliveryObligationRef)
            or event.stage is not PaneDeliveryStage.PAINT_RETURNED):
        return ReadyToPaintTiming(PaintTimingState.INVALID_BOUNDARY)
    paint = event.paint_return
    if paint is None:
        return ReadyToPaintTiming(PaintTimingState.MISSING_PAINT_RECEIPT)
    if not isinstance(paint, PanePaintReturnReceipt) or paint.ref != event.ref:
        return ReadyToPaintTiming(PaintTimingState.INVALID_BOUNDARY)
    ready = event.ref.identity.ready
    if ready.mapping is not ReadyClockMapping.BOUNDED or ready.host_bounds is None:
        return ReadyToPaintTiming(PaintTimingState.UNMAPPED_READY)
    bounds = ready.host_bounds
    if bounds.host_clock is None:
        return ReadyToPaintTiming(PaintTimingState.UNKNOWN_HOST_CLOCK)
    if bounds.host_clock != paint.host_clock:
        return ReadyToPaintTiming(PaintTimingState.FOREIGN_HOST_CLOCK)
    # An after-return sample before every possible ready time contradicts
    # the observed data-to-paint ordering; don't repair it or change RF time.
    if paint.sampled_after_return_ns < bounds.earliest_host_ns:
        return ReadyToPaintTiming(PaintTimingState.CLOCK_ORDER_INVALID)
    bookkeeping = None
    if event.host_clock is not None:
        if not isinstance(event.host_clock, HostClockScope):
            return ReadyToPaintTiming(PaintTimingState.INVALID_BOUNDARY)
        if event.host_clock != paint.host_clock:
            return ReadyToPaintTiming(PaintTimingState.FOREIGN_HOST_CLOCK)
        if event.host_perf_ns is not None:
            if type(event.host_perf_ns) is not int or not 0 <= event.host_perf_ns < (1 << 63):
                return ReadyToPaintTiming(PaintTimingState.CLOCK_ORDER_INVALID)
            bookkeeping = event.host_perf_ns - paint.sampled_after_return_ns
            if bookkeeping < 0:
                return ReadyToPaintTiming(PaintTimingState.CLOCK_ORDER_INVALID)
    return_interval = (paint.before_paint_ns - bounds.latest_host_ns,
                       paint.sampled_after_return_ns - bounds.earliest_host_ns)
    sample_interval = (paint.sampled_after_return_ns - bounds.latest_host_ns,
                       paint.sampled_after_return_ns - bounds.earliest_host_ns)
    state = PaintTimingState.ORDER_UNCERTAIN if return_interval[0] < 0 else PaintTimingState.BOUNDED
    return ReadyToPaintTiming(state, return_interval, sample_interval, bookkeeping)
