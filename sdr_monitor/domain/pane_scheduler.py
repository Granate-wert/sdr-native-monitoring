"""Bounded, Qt-free R10-E4 multi-pane capture planning.

This module compiles immutable pane intent into either one shared capture with
read-only crop metadata or a finite time-slice cycle.  It opens no device,
does not retune, and never carries raw I/Q or spectrum arrays.  The resulting
control gaps are planned control boundaries, not estimates of ADC sample loss.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import math

from .receiver_topology import (
    AcquisitionGroup,
    PaneDisplayPolicy,
    PaneSchedulerPolicy,
    ReceiverBindingMode,
    SchedulerPolicyKind,
    SweepPaneRequest,
)


_MAX_PANES = 4
_MAX_RESOURCE_JOBS = 4
_MAX_SCHEDULE_SLOTS = 400


class PaneScheduleError(ValueError):
    """Raised before a pane plan could create unbounded or ambiguous RX work."""


class PaneControlGapReason(StrEnum):
    INITIAL_CONFIGURATION = "initial_configuration"
    PROFILE_OR_RF_PLAN_CHANGE = "profile_or_rf_plan_change"


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PaneScheduleError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise PaneScheduleError(f"{label} must not be blank")
    return normalized


def _positive_finite(value: float, label: str) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise PaneScheduleError(f"{label} must be finite and positive")
    return value


def _nonnegative_finite(value: float, label: str) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise PaneScheduleError(f"{label} must be finite and non-negative")
    return value


@dataclass(frozen=True, slots=True)
class CaptureEpochCost:
    """Declared bounded cost of one planned capture activation.

    The retune/settle part is represented separately as an explicit control
    gap whenever the RF plan changes.  The active part is one bounded capture,
    DSP and publication unit; none of these values is an observed rate.
    """

    retune_s: float
    settle_s: float
    capture_s: float
    dsp_s: float
    publication_s: float

    def __post_init__(self) -> None:
        for label in ("retune_s", "settle_s", "capture_s", "dsp_s", "publication_s"):
            _nonnegative_finite(getattr(self, label), label)
        if self.active_s <= 0.0:
            raise PaneScheduleError("at least one active capture/DSP/publication cost must be positive")

    @property
    def control_gap_s(self) -> float:
        return self.retune_s + self.settle_s

    @property
    def active_s(self) -> float:
        return self.capture_s + self.dsp_s + self.publication_s


@dataclass(frozen=True, slots=True)
class PaneCaptureProfile:
    """Exact RF/DSP/calibration compatibility signature for one pane request."""

    sample_rate_hz: float
    analog_bandwidth_hz: float
    gain_mode: str
    manual_gain_db: float | None
    fft_size: int
    hop_size: int
    window: str
    detector: str
    calibration_profile_id: str | None
    usable_capture_span_hz: float
    epoch_cost: CaptureEpochCost

    def __post_init__(self) -> None:
        _positive_finite(self.sample_rate_hz, "sample rate")
        _positive_finite(self.analog_bandwidth_hz, "analog bandwidth")
        _positive_finite(self.usable_capture_span_hz, "usable capture span")
        if self.analog_bandwidth_hz > self.sample_rate_hz:
            raise PaneScheduleError("analog bandwidth must not exceed the declared sample rate")
        object.__setattr__(self, "gain_mode", _required_text(self.gain_mode, "gain mode"))
        object.__setattr__(self, "window", _required_text(self.window, "window"))
        object.__setattr__(self, "detector", _required_text(self.detector, "detector"))
        if self.manual_gain_db is not None and not math.isfinite(self.manual_gain_db):
            raise PaneScheduleError("manual gain must be finite when supplied")
        if isinstance(self.fft_size, bool) or not isinstance(self.fft_size, int) or self.fft_size < 2:
            raise PaneScheduleError("FFT size must be an integer of at least two")
        if self.fft_size & (self.fft_size - 1):
            raise PaneScheduleError("FFT size must be a power of two")
        if isinstance(self.hop_size, bool) or not isinstance(self.hop_size, int) or not 1 <= self.hop_size <= self.fft_size:
            raise PaneScheduleError("FFT hop must be in [1, FFT size]")
        if self.calibration_profile_id is not None:
            object.__setattr__(
                self,
                "calibration_profile_id",
                _required_text(self.calibration_profile_id, "calibration profile id"),
            )

    @property
    def compatibility_key(self) -> tuple[object, ...]:
        """Fields that must be identical before two panes may share capture."""

        return (
            self.sample_rate_hz,
            self.analog_bandwidth_hz,
            self.gain_mode,
            self.manual_gain_db,
            self.fft_size,
            self.hop_size,
            self.window,
            self.detector,
            self.calibration_profile_id,
            self.usable_capture_span_hz,
            self.epoch_cost,
        )


@dataclass(frozen=True, slots=True)
class PaneSchedulerConfig:
    """Finite plan bounds; all excess requests are rejected before device I/O."""

    maximum_panes: int = _MAX_PANES
    maximum_resource_jobs: int = _MAX_RESOURCE_JOBS
    maximum_schedule_slots: int = _MAX_SCHEDULE_SLOTS

    def __post_init__(self) -> None:
        for label in ("maximum_panes", "maximum_resource_jobs", "maximum_schedule_slots"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise PaneScheduleError(f"{label} must be a positive integer")
        if self.maximum_panes > _MAX_PANES:
            raise PaneScheduleError(f"R10-E4 supports at most {_MAX_PANES} panes")
        if self.maximum_resource_jobs > _MAX_RESOURCE_JOBS:
            raise PaneScheduleError(f"R10-E4 supports at most {_MAX_RESOURCE_JOBS} capture jobs per resource")
        if self.maximum_schedule_slots > _MAX_SCHEDULE_SLOTS:
            raise PaneScheduleError(f"R10-E4 supports at most {_MAX_SCHEDULE_SLOTS} schedule slots")


@dataclass(frozen=True, slots=True)
class PaneCrop:
    """A read-only frequency view of one admitted shared capture plan."""

    pane_id: str
    receiver_endpoint_id: str
    start_hz: float
    stop_hz: float
    display_policy: PaneDisplayPolicy = PaneDisplayPolicy.VISIBLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "pane_id", _required_text(self.pane_id, "pane id"))
        object.__setattr__(
            self,
            "receiver_endpoint_id",
            _required_text(self.receiver_endpoint_id, "receiver endpoint id"),
        )
        object.__setattr__(self, "display_policy", PaneDisplayPolicy(self.display_policy))
        if not math.isfinite(self.start_hz) or not math.isfinite(self.stop_hz) or self.stop_hz <= self.start_hz:
            raise PaneScheduleError("pane crop span must be finite and increasing")


@dataclass(frozen=True, slots=True)
class CaptureJob:
    """One bounded native capture plan; it contains no acquired samples."""

    capture_id: str
    physical_stream_resource_id: str
    receiver_endpoint_ids: tuple[str, ...]
    mode: ReceiverBindingMode
    start_hz: float
    stop_hz: float
    profile_ids: tuple[str, ...]
    profile: PaneCaptureProfile
    crops: tuple[PaneCrop, ...]
    scheduler_policy: PaneSchedulerPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "capture_id", _required_text(self.capture_id, "capture id"))
        object.__setattr__(self, "physical_stream_resource_id", _required_text(self.physical_stream_resource_id, "physical stream resource id"))
        endpoint_ids = tuple(
            _required_text(item, "receiver endpoint id") for item in self.receiver_endpoint_ids
        )
        if not endpoint_ids or len(set(endpoint_ids)) != len(endpoint_ids):
            raise PaneScheduleError("capture job receiver endpoint ids must be non-empty and unique")
        object.__setattr__(self, "receiver_endpoint_ids", endpoint_ids)
        object.__setattr__(self, "mode", ReceiverBindingMode(self.mode))
        if not math.isfinite(self.start_hz) or not math.isfinite(self.stop_hz) or self.stop_hz <= self.start_hz:
            raise PaneScheduleError("capture span must be finite and increasing")
        if self.stop_hz - self.start_hz > self.profile.usable_capture_span_hz:
            raise PaneScheduleError("capture span exceeds the profile usable capture span")
        profile_ids = tuple(_required_text(item, "profile id") for item in self.profile_ids)
        if not profile_ids:
            raise PaneScheduleError("capture job must retain at least one profile id")
        object.__setattr__(self, "profile_ids", profile_ids)
        crops = tuple(self.crops)
        if not crops or len({crop.pane_id for crop in crops}) != len(crops):
            raise PaneScheduleError("capture job crops must be non-empty with unique pane ids")
        if any(crop.start_hz < self.start_hz or crop.stop_hz > self.stop_hz for crop in crops):
            raise PaneScheduleError("pane crop must be inside its capture span")
        if any(crop.receiver_endpoint_id not in endpoint_ids for crop in crops):
            raise PaneScheduleError("pane crop endpoint must belong to its capture job")
        if self.mode is ReceiverBindingMode.SHARED_CAPTURE and len(crops) < 2:
            raise PaneScheduleError("shared capture requires at least two pane crops")
        if self.mode is not ReceiverBindingMode.SHARED_CAPTURE and len(crops) != 1:
            raise PaneScheduleError("non-shared capture must own exactly one pane crop")
        object.__setattr__(self, "crops", crops)

    @property
    def configuration_key(self) -> tuple[object, ...]:
        # Different RF span means a different activation even with a matching
        # DSP profile, because it can require a centre-frequency retune.
        return (self.receiver_endpoint_ids, self.start_hz, self.stop_hz, self.profile.compatibility_key)


@dataclass(frozen=True, slots=True)
class PaneControlGap:
    """Planned control boundary; it is never represented as missing samples."""

    physical_stream_resource_id: str
    previous_capture_id: str | None
    next_capture_id: str
    reason: PaneControlGapReason
    planned_duration_s: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "physical_stream_resource_id", _required_text(self.physical_stream_resource_id, "physical stream resource id"))
        if self.previous_capture_id is not None:
            object.__setattr__(self, "previous_capture_id", _required_text(self.previous_capture_id, "previous capture id"))
        object.__setattr__(self, "next_capture_id", _required_text(self.next_capture_id, "next capture id"))
        object.__setattr__(self, "reason", PaneControlGapReason(self.reason))
        _nonnegative_finite(self.planned_duration_s, "planned control gap duration")
        if self.reason is PaneControlGapReason.INITIAL_CONFIGURATION and self.previous_capture_id is not None:
            raise PaneScheduleError("initial configuration gap cannot have a previous capture")
        if self.reason is PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE and self.previous_capture_id is None:
            raise PaneScheduleError("transition gap requires a previous capture")


@dataclass(frozen=True, slots=True)
class PaneScheduleSlot:
    """One planned active capture unit in a finite recurring schedule."""

    capture_id: str
    cycle_epoch_offset: int
    relative_start_s: float
    active_duration_s: float
    control_gap_before: PaneControlGap | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capture_id", _required_text(self.capture_id, "capture id"))
        if isinstance(self.cycle_epoch_offset, bool) or not isinstance(self.cycle_epoch_offset, int) or self.cycle_epoch_offset < 1:
            raise PaneScheduleError("cycle epoch offset must be a positive integer")
        _nonnegative_finite(self.relative_start_s, "slot relative start")
        _positive_finite(self.active_duration_s, "slot active duration")


@dataclass(frozen=True, slots=True)
class PaneRevisitEstimate:
    """Deterministic schedule estimate; it is not observed pane LPS."""

    pane_id: str
    physical_stream_resource_id: str
    mode: ReceiverBindingMode
    visits_per_cycle: int
    maximum_revisit_s: float
    requested_maximum_revisit_s: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pane_id", _required_text(self.pane_id, "pane id"))
        object.__setattr__(self, "physical_stream_resource_id", _required_text(self.physical_stream_resource_id, "physical stream resource id"))
        object.__setattr__(self, "mode", ReceiverBindingMode(self.mode))
        if isinstance(self.visits_per_cycle, bool) or not isinstance(self.visits_per_cycle, int) or self.visits_per_cycle < 1:
            raise PaneScheduleError("visits per cycle must be a positive integer")
        _positive_finite(self.maximum_revisit_s, "maximum revisit")
        if self.requested_maximum_revisit_s is not None:
            _positive_finite(self.requested_maximum_revisit_s, "requested maximum revisit")


@dataclass(frozen=True, slots=True)
class ResourcePaneSchedule:
    """All immutable capture jobs for one physical stream resource."""

    physical_stream_resource_id: str
    jobs: tuple[CaptureJob, ...]
    initial_control_gap: PaneControlGap
    slots: tuple[PaneScheduleSlot, ...]
    cycle_transition_gap: PaneControlGap | None
    cycle_duration_s: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "physical_stream_resource_id", _required_text(self.physical_stream_resource_id, "physical stream resource id"))
        jobs = tuple(self.jobs)
        slots = tuple(self.slots)
        if not jobs or not slots:
            raise PaneScheduleError("resource schedule requires jobs and slots")
        if len({job.capture_id for job in jobs}) != len(jobs):
            raise PaneScheduleError("resource schedule capture ids must be unique")
        if any(job.physical_stream_resource_id != self.physical_stream_resource_id for job in jobs):
            raise PaneScheduleError("resource schedule job belongs to another physical resource")
        known_jobs = {job.capture_id for job in jobs}
        if any(slot.capture_id not in known_jobs for slot in slots):
            raise PaneScheduleError("schedule slot refers to an unknown capture job")
        if self.initial_control_gap.physical_stream_resource_id != self.physical_stream_resource_id:
            raise PaneScheduleError("initial control gap belongs to another physical resource")
        if self.cycle_transition_gap is not None and self.cycle_transition_gap.physical_stream_resource_id != self.physical_stream_resource_id:
            raise PaneScheduleError("cycle transition gap belongs to another physical resource")
        _positive_finite(self.cycle_duration_s, "cycle duration")
        object.__setattr__(self, "jobs", jobs)
        object.__setattr__(self, "slots", slots)


@dataclass(frozen=True, slots=True)
class PaneSchedule:
    """Final bounded plan. It is a control artefact, not an RX execution result."""

    resources: tuple[ResourcePaneSchedule, ...]
    pane_revisits: tuple[PaneRevisitEstimate, ...]

    def __post_init__(self) -> None:
        resources = tuple(self.resources)
        revisits = tuple(self.pane_revisits)
        if not resources or not revisits:
            raise PaneScheduleError("pane schedule requires resources and revisit estimates")
        if len({resource.physical_stream_resource_id for resource in resources}) != len(resources):
            raise PaneScheduleError("pane schedule resource ids must be unique")
        if len({item.pane_id for item in revisits}) != len(revisits):
            raise PaneScheduleError("pane schedule revisit pane ids must be unique")
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "pane_revisits", revisits)


@dataclass(frozen=True, slots=True)
class _ResolvedPane:
    request: SweepPaneRequest
    group: AcquisitionGroup
    profile_id: str
    profile: PaneCaptureProfile


def _validate_groups(groups: Sequence[AcquisitionGroup]) -> dict[str, AcquisitionGroup]:
    by_endpoint: dict[str, AcquisitionGroup] = {}
    resource_ids: set[str] = set()
    for group in groups:
        if group.physical_stream_resource_id in resource_ids:
            raise PaneScheduleError("one E4 acquisition group is required for each physical stream resource")
        resource_ids.add(group.physical_stream_resource_id)
        for endpoint in group.endpoints:
            if endpoint.endpoint_id in by_endpoint:
                raise PaneScheduleError("receiver endpoint ids must be unique across scheduler groups")
            by_endpoint[endpoint.endpoint_id] = group
    if not by_endpoint:
        raise PaneScheduleError("at least one acquisition group is required")
    return by_endpoint


def _resolve_panes(
    groups: Sequence[AcquisitionGroup],
    requests: Sequence[SweepPaneRequest],
    profiles: Mapping[str, PaneCaptureProfile],
    config: PaneSchedulerConfig,
) -> tuple[_ResolvedPane, ...]:
    endpoints = _validate_groups(groups)
    entries = tuple(requests)
    if not entries or len(entries) > config.maximum_panes:
        raise PaneScheduleError(f"scheduler accepts 1..{config.maximum_panes} pane requests")
    if len({request.pane_id for request in entries}) != len(entries):
        raise PaneScheduleError("pane ids must be unique")
    resolved: list[_ResolvedPane] = []
    for request in entries:
        group = endpoints.get(request.receiver_endpoint_id)
        if group is None:
            raise PaneScheduleError("pane request refers to an endpoint outside the admitted acquisition groups")
        if request.profile_id is None:
            raise PaneScheduleError("R10-E4 requires an explicit capture profile for every pane")
        profile = profiles.get(request.profile_id)
        if profile is None:
            raise PaneScheduleError("pane request refers to an unknown capture profile")
        if request.stop_hz - request.start_hz > profile.usable_capture_span_hz:
            raise PaneScheduleError("pane RF span exceeds the profile usable capture span")
        resolved.append(_ResolvedPane(request, group, request.profile_id, profile))
    return tuple(resolved)


def _shared_jobs(
    resource_id: str,
    entries: Sequence[_ResolvedPane],
    capture_index: int,
) -> tuple[list[CaptureJob], int]:
    buckets: dict[tuple[object, ...], list[_ResolvedPane]] = {}
    for item in entries:
        # A group contains non-overlapping RX-chain endpoint selections.  Same
        # profile/overlap panes from RX1 and RX2 can therefore reuse one future
        # common-LO/buffer capture exactly as same-endpoint panes can.  A job
        # still retains endpoint identity on every crop for later E2 fan-out.
        key = item.profile.compatibility_key
        buckets.setdefault(key, []).append(item)

    jobs: list[CaptureJob] = []
    for key in sorted(buckets, key=repr):
        ordered = sorted(buckets[key], key=lambda item: (item.request.start_hz, item.request.stop_hz, item.request.pane_id))
        component: list[_ResolvedPane] = []
        component_stop = -math.inf
        for item in ordered:
            if component and item.request.start_hz >= component_stop:
                jobs.append(_make_shared_job(resource_id, component, capture_index))
                capture_index += 1
                component = []
                component_stop = -math.inf
            component.append(item)
            component_stop = max(component_stop, item.request.stop_hz)
        if component:
            jobs.append(_make_shared_job(resource_id, component, capture_index))
            capture_index += 1
    return jobs, capture_index


def _make_shared_job(
    resource_id: str,
    component: Sequence[_ResolvedPane],
    capture_index: int,
) -> CaptureJob:
    if len(component) < 2:
        raise PaneScheduleError("shared-capture pane requires a compatible overlapping peer")
    profile = component[0].profile
    policies = {item.request.scheduler_policy for item in component}
    if len(policies) != 1:
        raise PaneScheduleError("shared-capture panes require one identical scheduler policy")
    start_hz = min(item.request.start_hz for item in component)
    stop_hz = max(item.request.stop_hz for item in component)
    if stop_hz - start_hz > profile.usable_capture_span_hz:
        raise PaneScheduleError("compatible overlapping shared-capture panes exceed one usable capture window")
    crops = tuple(
        PaneCrop(
            item.request.pane_id,
            item.request.receiver_endpoint_id,
            item.request.start_hz,
            item.request.stop_hz,
            item.request.display_policy,
        )
        for item in sorted(component, key=lambda item: item.request.pane_id)
    )
    return CaptureJob(
        f"{resource_id}:capture:{capture_index}",
        resource_id,
        tuple(sorted({item.request.receiver_endpoint_id for item in component})),
        ReceiverBindingMode.SHARED_CAPTURE,
        start_hz,
        stop_hz,
        tuple(sorted(item.profile_id for item in component)),
        profile,
        crops,
        component[0].request.scheduler_policy,
    )


def _single_job(item: _ResolvedPane, capture_index: int) -> CaptureJob:
    request = item.request
    return CaptureJob(
        f"{item.group.physical_stream_resource_id}:capture:{capture_index}",
        item.group.physical_stream_resource_id,
        (request.receiver_endpoint_id,),
        request.requested_binding_mode,
        request.start_hz,
        request.stop_hz,
        (item.profile_id,),
        item.profile,
        (
            PaneCrop(
                request.pane_id,
                request.receiver_endpoint_id,
                request.start_hz,
                request.stop_hz,
                request.display_policy,
            ),
        ),
        request.scheduler_policy,
    )


def _jobs_for_resource(
    resource_id: str,
    entries: Sequence[_ResolvedPane],
    config: PaneSchedulerConfig,
) -> tuple[CaptureJob, ...]:
    dedicated = [item for item in entries if item.request.requested_binding_mode is ReceiverBindingMode.DEDICATED_PARALLEL]
    if dedicated:
        if len(entries) != 1:
            raise PaneScheduleError("a dedicated-parallel pane cannot share a physical stream resource in R10-E4")
        return (_single_job(dedicated[0], 0),)

    shared = [item for item in entries if item.request.requested_binding_mode is ReceiverBindingMode.SHARED_CAPTURE]
    time_sliced = [item for item in entries if item.request.requested_binding_mode is ReceiverBindingMode.TIME_SLICED]
    shared_jobs, next_index = _shared_jobs(resource_id, shared, 0)
    jobs = shared_jobs + [_single_job(item, next_index + offset) for offset, item in enumerate(sorted(time_sliced, key=lambda item: item.request.pane_id))]
    if not jobs or len(jobs) > config.maximum_resource_jobs:
        raise PaneScheduleError(f"resource {resource_id} exceeds its bounded capture-job capacity")
    return tuple(jobs)


def _weighted_cycle(jobs: Sequence[CaptureJob], config: PaneSchedulerConfig) -> tuple[CaptureJob, ...]:
    total_weight = sum(job.scheduler_policy.weight for job in jobs)
    if total_weight > config.maximum_schedule_slots:
        raise PaneScheduleError("weighted cycle exceeds its bounded slot capacity")
    current = [0 for _job in jobs]
    sequence: list[CaptureJob] = []
    for _index in range(total_weight):
        for item_index, job in enumerate(jobs):
            current[item_index] += job.scheduler_policy.weight
        selected = min(
            range(len(jobs)),
            key=lambda item_index: (-current[item_index], jobs[item_index].capture_id),
        )
        current[selected] -= total_weight
        sequence.append(jobs[selected])
    return tuple(sequence)


def _control_gap(
    resource_id: str,
    previous: CaptureJob | None,
    following: CaptureJob,
) -> PaneControlGap | None:
    if previous is not None and previous.configuration_key == following.configuration_key:
        return None
    return PaneControlGap(
        resource_id,
        None if previous is None else previous.capture_id,
        following.capture_id,
        PaneControlGapReason.INITIAL_CONFIGURATION if previous is None else PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE,
        following.profile.epoch_cost.control_gap_s,
    )


def _schedule_resource(resource_id: str, jobs: tuple[CaptureJob, ...], config: PaneSchedulerConfig) -> ResourcePaneSchedule:
    sequence: tuple[CaptureJob, ...]
    if len(jobs) == 1 and jobs[0].mode is ReceiverBindingMode.DEDICATED_PARALLEL:
        sequence = tuple(jobs)
    else:
        sequence = _weighted_cycle(jobs, config)
    initial = _control_gap(resource_id, None, sequence[0])
    assert initial is not None

    slots: list[PaneScheduleSlot] = []
    epoch_offset = 1
    elapsed_s = 0.0
    previous: CaptureJob | None = None
    for job in sequence:
        gap = _control_gap(resource_id, previous, job) if previous is not None else None
        if gap is not None:
            elapsed_s += gap.planned_duration_s
            epoch_offset += 1
        slots.append(PaneScheduleSlot(job.capture_id, epoch_offset, elapsed_s, job.profile.epoch_cost.active_s, gap))
        elapsed_s += job.profile.epoch_cost.active_s
        previous = job
    transition = _control_gap(resource_id, previous, sequence[0])
    if transition is not None:
        elapsed_s += transition.planned_duration_s
    return ResourcePaneSchedule(resource_id, jobs, initial, tuple(slots), transition, elapsed_s)


def _revisit_estimates(schedule: ResourcePaneSchedule) -> tuple[PaneRevisitEstimate, ...]:
    jobs = {job.capture_id: job for job in schedule.jobs}
    starts_by_pane: dict[str, list[float]] = {}
    for slot in schedule.slots:
        for crop in jobs[slot.capture_id].crops:
            starts_by_pane.setdefault(crop.pane_id, []).append(slot.relative_start_s)
    estimates: list[PaneRevisitEstimate] = []
    for job in schedule.jobs:
        for crop in job.crops:
            starts = starts_by_pane[crop.pane_id]
            maximum_revisit_s = max(
                next_start - start
                for start, next_start in zip(starts, (*starts[1:], starts[0] + schedule.cycle_duration_s), strict=True)
            )
            requested = (
                job.scheduler_policy.minimum_revisit_s
                if job.scheduler_policy.kind is SchedulerPolicyKind.MINIMUM_REVISIT
                else None
            )
            if requested is not None and maximum_revisit_s > requested:
                raise PaneScheduleError(
                    f"pane {crop.pane_id} cannot meet its maximum revisit deadline before RX admission"
                )
            estimates.append(
                PaneRevisitEstimate(
                    crop.pane_id,
                    schedule.physical_stream_resource_id,
                    job.mode,
                    len(starts),
                    maximum_revisit_s,
                    requested,
                )
            )
    return tuple(sorted(estimates, key=lambda item: item.pane_id))


def compile_pane_schedule(
    groups: Sequence[AcquisitionGroup],
    requests: Sequence[SweepPaneRequest],
    profiles: Mapping[str, PaneCaptureProfile],
    config: PaneSchedulerConfig | None = None,
) -> PaneSchedule:
    """Compile finite shared-capture/time-slice control plans without RX I/O.

    ``minimum_revisit_s`` in the pre-existing policy is interpreted as a hard
    *maximum permitted* inter-visit interval.  The returned revisit values are
    deterministic schedule estimates, not measured LPS or device throughput.
    """

    config = config or PaneSchedulerConfig()
    resolved = _resolve_panes(groups, requests, profiles, config)
    by_resource: dict[str, list[_ResolvedPane]] = {}
    for item in resolved:
        by_resource.setdefault(item.group.physical_stream_resource_id, []).append(item)

    resources: list[ResourcePaneSchedule] = []
    revisits: list[PaneRevisitEstimate] = []
    for resource_id in sorted(by_resource):
        jobs = _jobs_for_resource(resource_id, by_resource[resource_id], config)
        resource_schedule = _schedule_resource(resource_id, jobs, config)
        resources.append(resource_schedule)
        revisits.extend(_revisit_estimates(resource_schedule))
    return PaneSchedule(tuple(resources), tuple(sorted(revisits, key=lambda item: item.pane_id)))


__all__ = [
    "CaptureEpochCost",
    "CaptureJob",
    "PaneCaptureProfile",
    "PaneControlGap",
    "PaneControlGapReason",
    "PaneCrop",
    "PaneRevisitEstimate",
    "PaneSchedule",
    "PaneScheduleError",
    "PaneScheduleSlot",
    "PaneSchedulerConfig",
    "ResourcePaneSchedule",
    "compile_pane_schedule",
]
