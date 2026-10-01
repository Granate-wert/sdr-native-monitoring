"""Full-profile RF intent for the default shared Analyzer, with no I/O.

This is not a Start permit, hardware readback or a replacement pane planner.
The application repeats capability/native admission and exact context checks
inside its established receiver/recorder transaction before any control effect.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import copysign, floor, isfinite

from .analyzer_session import AnalyzerMode, AnalyzerPhase, AnalyzerSessionState
from ..domain.analyzer_sources import AnalyzerSourceChoice
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.device_capabilities import DeviceCapabilityInventory, DeviceFamily
from ..domain.hackrf_live import HackrfLiveRequest
from ..domain.hackrf_sweep import HackrfSweepRequest
from ..domain.live import LiveConfiguration, LiveSnapshot
from ..domain.ad936x_route_capabilities import Ad936xRouteCapabilities
from ..domain.tinysa_analyzer import TinySaSweepRequest


AnalyzerRfRequest = LiveConfiguration | HackrfLiveRequest | ContinuousSweepPlanRequest | HackrfSweepRequest | TinySaSweepRequest


class AnalyzerRfChangeRejected(ValueError):
    """Missing/stale RF intent or inadmissible range; no implicit fallback."""


class AnalyzerRfStopRejected(AnalyzerRfChangeRejected):
    """Admission failed BEFORE Stop; acquisition has not been dispatched here."""


@dataclass(frozen=True, slots=True)
class AnalyzerRfContext:
    source: AnalyzerSourceChoice
    selection_revision: int
    state: AnalyzerSessionState
    request: AnalyzerRfRequest
    # Only AD Sweep needs both a Live DSP profile and a Sweep plan. Never
    # fabricate a Pluto profile for HackRF or an instrument trace.
    applied_live: LiveConfiguration | None = None
    # Scalar producer/control identity only, never sequence or a frame array.
    session_id: str | None = None
    configuration_generation: int | None = None
    acquisition_epoch: int | None = None
    receiver_id: str | None = None
    clock_domain: str | None = None
    unit: str | None = None
    # Exact selected descriptor reference ONLY for empty-serial AD routes.
    # Never enters source_inventory, stable identity or calibration admission.
    route_rf_capabilities: Ad936xRouteCapabilities | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.source, AnalyzerSourceChoice)
                or type(self.selection_revision) is not int or not 0 <= self.selection_revision < 1 << 64
                or not isinstance(self.state, AnalyzerSessionState)
                or not isinstance(self.state.mode, AnalyzerMode) or not isinstance(self.state.phase, AnalyzerPhase)
                or type(self.state.operation_id) is not int or not 0 <= self.state.operation_id < 1 << 64
                or self.state.phase not in (AnalyzerPhase.IDLE, AnalyzerPhase.RUNNING)
                or self.state.error is not None):
            raise AnalyzerRfChangeRejected("RF context requires a stable selected Analyzer operation")
        request, family, mode = self.request, self.source.family, self.state.mode
        route = self.route_rf_capabilities
        if route is not None:
            if (family is not DeviceFamily.AD936X or not isinstance(route, Ad936xRouteCapabilities)
                    or self.source.binding.snapshot is not None or self.source.binding.calibration_identity is not None):
                raise AnalyzerRfChangeRejected("RF route facts cannot replace a canonical identity")
            route.__post_init__()
        expected = {(DeviceFamily.AD936X, AnalyzerMode.RTBW): LiveConfiguration,
                    (DeviceFamily.AD936X, AnalyzerMode.SWEEP): ContinuousSweepPlanRequest,
                    (DeviceFamily.HACKRF, AnalyzerMode.RTBW): HackrfLiveRequest,
                    (DeviceFamily.HACKRF, AnalyzerMode.SWEEP): HackrfSweepRequest,
                    (DeviceFamily.TINYSA, AnalyzerMode.SWEEP): TinySaSweepRequest}.get((family, mode))
        if expected is None or not isinstance(request, expected):
            raise AnalyzerRfChangeRejected("RF context requires its complete family/mode profile")
        ad_sweep = isinstance(request, ContinuousSweepPlanRequest)
        if ad_sweep != isinstance(self.applied_live, LiveConfiguration):
            raise AnalyzerRfChangeRejected("Only AD Sweep requires a retained Live profile")
        if self.applied_live is not None:
            self.applied_live.__post_init__()
        request.__post_init__()  # Forged frozen payloads do not bypass constructors.
        if isinstance(request, (ContinuousSweepPlanRequest, HackrfSweepRequest, TinySaSweepRequest)):
            if (self.state.operation_id <= 0 or self.state.sweep_epoch != request.epoch):
                raise AnalyzerRfChangeRejected("RF context requires the actually accepted Sweep epoch")
        if isinstance(request, (HackrfSweepRequest, TinySaSweepRequest)):
            if request.source is not self.source or request.selection_revision != self.selection_revision:
                raise AnalyzerRfChangeRejected("RF context source selection is stale")
        if isinstance(request, HackrfLiveRequest) and request.source_id != self.source.device_id:
            raise AnalyzerRfChangeRejected("RF context carries another HackRF source")
        for value in (self.configuration_generation, self.acquisition_epoch):
            if value is not None and (type(value) is not int or not 0 <= value < 1 << 64):
                raise AnalyzerRfChangeRejected("RF context generation/epoch must be explicit uint64 or unknown")

    def matches(self, current: AnalyzerRfContext, *, stopped: bool = False) -> bool:
        """Fresh frames do not invalidate intent; a new control operation does.

        A confirmed Stop may clear active producer metadata. It may not change
        source, operation, configuration or any retained non-frequency field.
        This predicate alone is not an atomic transaction or a Start permit.
        """
        if not isinstance(current, AnalyzerRfContext):
            return False
        required_phase = AnalyzerPhase.IDLE if stopped else self.state.phase
        if (current.source is not self.source or current.selection_revision != self.selection_revision
                or current.state != replace(self.state, phase=required_phase)
                or current.request != self.request or current.applied_live != self.applied_live
                or current.route_rf_capabilities is not self.route_rf_capabilities
                or current.configuration_generation != self.configuration_generation
                or current.session_id != self.session_id):
            return False
        return stopped or (current.acquisition_epoch == self.acquisition_epoch
                           and current.receiver_id == self.receiver_id
                           and current.clock_domain == self.clock_domain and current.unit == self.unit)


def source_inventory(context: AnalyzerRfContext) -> DeviceCapabilityInventory:
    """Immutable view of the EXACT selected observed facts, not a new catalog."""
    source = context.source
    binding = source.binding
    if binding.snapshot is None or binding.calibration_identity is None or source.runtime is None:
        raise AnalyzerRfChangeRejected("RF source capability/runtime evidence is missing")
    return DeviceCapabilityInventory((binding.snapshot,), bindings=(binding,), runtimes=(source.runtime,))


def _shift_request(context: AnalyzerRfContext, shift_hz: float) -> tuple[AnalyzerRfRequest, float, float]:
    if not isinstance(context, AnalyzerRfContext) or type(shift_hz) not in (int, float):
        raise AnalyzerRfChangeRejected("RF shift must be a finite offset of an exact profile")
    try:
        offset = float(shift_hz)
    except OverflowError:
        raise AnalyzerRfChangeRejected("RF shift must be a finite offset of an exact profile") from None
    if not isfinite(offset):
        raise AnalyzerRfChangeRejected("RF shift must be a finite offset of an exact profile")
    context.__post_init__()
    quantum = 1_000_000.0 if isinstance(context.request, HackrfSweepRequest) else 1.0
    effective = copysign(floor(abs(offset) / quantum + 0.5) * quantum, offset)
    if effective == 0 or not isfinite(effective):
        raise AnalyzerRfChangeRejected("RF shift is smaller than the admitted request quantum")
    request = context.request
    if isinstance(request, LiveConfiguration):
        shifted: AnalyzerRfRequest = replace(request, center_hz=request.center_hz + effective)
    elif isinstance(request, HackrfLiveRequest):
        shifted = replace(request, center_frequency_hz=request.center_frequency_hz + effective)
    elif isinstance(request, ContinuousSweepPlanRequest):
        shifted = replace(request, start_hz=request.start_hz + effective, stop_hz=request.stop_hz + effective)
    else:
        shifted = replace(request, start_hz=request.start_hz + int(effective), stop_hz=request.stop_hz + int(effective))
    return shifted, effective, quantum


@dataclass(frozen=True, slots=True)
class AnalyzerRfShiftProposal:
    expected: AnalyzerRfContext
    request: AnalyzerRfRequest
    requested_shift_hz: float
    effective_shift_hz: float
    quantum_hz: float

    def __post_init__(self) -> None:
        request, effective, quantum = _shift_request(self.expected, self.requested_shift_hz)
        if (self.request != request or type(self.effective_shift_hz) not in (int, float)
                or type(self.quantum_hz) not in (int, float)
                or self.effective_shift_hz != effective or self.quantum_hz != quantum):
            raise AnalyzerRfChangeRejected("RF proposal may change only the rounded frequency fields")
        if isinstance(self.request, (HackrfSweepRequest, TinySaSweepRequest)) and self.request.source is not self.expected.source:
            raise AnalyzerRfChangeRejected("RF proposal must retain the exact selected source reference")


def compile_analyzer_rf_shift(context: AnalyzerRfContext, shift_hz: float) -> AnalyzerRfShiftProposal:
    """All five typed strategies preserve every field outside RF frequency.

    No range clamping, source substitution, Fs/FFT lowering, epoch or generation
    allocation. Actual readback and next Start remain the existing owners' job.
    Capability/native preflight is performed by the application, not implied
    by this pure compiler succeeding.
    """
    request, effective, quantum = _shift_request(context, shift_hz)
    return AnalyzerRfShiftProposal(context, request, float(shift_hz), effective, quantum)


@dataclass(frozen=True, slots=True)
class AnalyzerRfApplyReceipt:
    """Exact stopped profile, NOT a measured frame or a reusable Start permit.

The application also requires this exact returned object's identity. Sweep
keeps its complete proposed host intent here until the existing facade assigns
a fresh actual epoch. The UI must acknowledge all fanout history changes before
requesting Start. A failed or superseded receipt cannot restart a receiver.
"""

    proposal: AnalyzerRfShiftProposal
    armed_context: AnalyzerRfContext
    snapshot: LiveSnapshot

    def __post_init__(self) -> None:
        if (not isinstance(self.proposal, AnalyzerRfShiftProposal)
                or not isinstance(self.armed_context, AnalyzerRfContext)
                or not isinstance(self.snapshot, LiveSnapshot)):
            raise AnalyzerRfChangeRejected("RF Apply requires a typed stopped receipt")
        self.proposal.__post_init__()
        self.armed_context.__post_init__()
        expected, armed = self.proposal.expected, self.armed_context
        if (armed.state.phase is not AnalyzerPhase.IDLE
                or armed.source is not expected.source or armed.selection_revision != expected.selection_revision
                or armed.route_rf_capabilities is not expected.route_rf_capabilities
                or armed.state != replace(expected.state, phase=AnalyzerPhase.IDLE)
                or self.snapshot.error is not None or self.snapshot.stop_required):
            raise AnalyzerRfChangeRejected("RF Apply receipt is not the confirmed stopped operation")
        if armed.state.mode is AnalyzerMode.SWEEP:
            # The last dispatched Sweep remains historical until NEXT Start.
            if armed.request != expected.request or armed.applied_live != expected.applied_live:
                raise AnalyzerRfChangeRejected("RF Sweep Apply must preserve its exact retained profile")
        else:
            request = self.proposal.request
            if isinstance(request, HackrfLiveRequest):
                request = replace(request, configuration_generation=armed.request.configuration_generation
                                  if isinstance(armed.request, HackrfLiveRequest) else 0)
            if armed.request != request:
                raise AnalyzerRfChangeRejected("RF Apply changed fields outside the admitted full profile")


__all__ = ["AnalyzerRfContext", "AnalyzerRfRequest", "AnalyzerRfChangeRejected", "AnalyzerRfStopRejected",
           "AnalyzerRfShiftProposal", "AnalyzerRfApplyReceipt", "compile_analyzer_rf_shift"]
