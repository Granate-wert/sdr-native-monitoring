"""Explicit APP-07 multi-resource control boundary, without Qt or SDR I/O.

The caller supplies one inert owner adapter per physical stream resource. This
session never discovers or opens a device in its constructor, and never infers
an RF path from digital I/Q scan elements. A future product adapter must prove
that its owner applies a CaptureJob and reports the resulting native identity.
"""

from __future__ import annotations

from _thread import LockType
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from math import isclose, isfinite
from threading import Lock, RLock
from time import monotonic
from typing import Callable, ContextManager, Iterator, Mapping, Protocol

import numpy as np

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.domain.paired_sweep import PairedSweepRunIdentity
from sdr_monitor.domain.pane_scheduler import (
    CaptureJob, CaptureMeasurementMode, PaneControlGap, PaneControlGapReason, PaneCrop, PaneRevisitEstimate, PaneSchedule,
    ResourcePaneSchedule, SpectrumTracePaneProfile, Ad936xPairedSweepPaneProfile,
)
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverEndpoint, SpectrumTraceEndpoint
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection

from .receiver_lease_manager import ReceiverLease, ReceiverLeaseManager
from .pane_resource_diagnostics import (
    PaneDiagnosticError, PaneFailureReason, PaneFailureStage, pane_failure_from_exception,
)


class PaneResourceError(PaneDiagnosticError):
    """Fixed control-plane refusal; vendor exception text is never published."""


@dataclass(frozen=True, slots=True)
class PaneCaptureAdmission:
    """Owner-confirmed producer contract for one logical capture activation.

    Applied RTBW geometry must match the scheduled profile. A Sweep producer
    cannot borrow an RTBW receipt; neither kind implies RF calibration proof.
    """

    capture_id: str
    source_id: str
    mode: CaptureMeasurementMode
    unit: str
    receiver_endpoint_ids: tuple[str, ...]
    acquisition_epoch: int
    session_id: str | None = None
    config_generation: int | None = None
    sample_rate_hz: float | None = None
    fft_size: int | None = None
    hop_size: int | None = None
    trace_points: int | None = None
    instrument_model_id: str | None = None
    instrument_identity_key: str | None = None
    firmware_fingerprint: str | None = None
    # The operational device route above is not necessarily a frame producer.
    # Paired native RX keeps its two caller-provided SourceDescriptors intact.
    endpoint_source_ids: tuple[tuple[str, str], ...] = ()
    paired_sweep_run: PairedSweepRunIdentity | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.capture_id, self.source_id, self.unit)):
            raise ValueError("capture admission identity, mode and unit are required")
        object.__setattr__(self, "mode", CaptureMeasurementMode(self.mode))
        endpoints = tuple(self.receiver_endpoint_ids)
        if not endpoints or len(set(endpoints)) != len(endpoints) or any(
                not isinstance(value, str) or not value for value in endpoints):
            raise ValueError("capture admission requires exact receiver endpoint identities")
        object.__setattr__(self, "receiver_endpoint_ids", endpoints)
        bindings = tuple(self.endpoint_source_ids)
        if bindings and (
                len(bindings) != len(endpoints)
                or any(not isinstance(item, tuple) or len(item) != 2
                       or any(not isinstance(value, str) or not value or value != value.strip()
                              for value in item) for item in bindings)
                or tuple(item[0] for item in bindings) != endpoints
                or len({item[1] for item in bindings}) != len(bindings)
                or (self.mode is not CaptureMeasurementMode.RTBW and self.paired_sweep_run is None)):
            raise ValueError("endpoint producer bindings require exact ordered RTBW endpoint/source identities")
        object.__setattr__(self, "endpoint_source_ids", bindings)
        if type(self.acquisition_epoch) is not int or self.acquisition_epoch < 0:
            raise ValueError("capture admission requires a non-negative producer epoch")
        if self.config_generation is not None and (
                type(self.config_generation) is not int or self.config_generation < 0):
            raise ValueError("capture admission generation must be a non-negative integer")
        if self.mode is CaptureMeasurementMode.INSTRUMENT_TRACE:
            if (self.sample_rate_hz is not None or self.fft_size is not None or self.hop_size is not None
                    or type(self.trace_points) is not int or not 2 <= self.trace_points <= 10001
                    or self.instrument_model_id not in {"tinysa_basic", "tinysa_ultra"}
                    or self.config_generation is None
                    or any(not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71
                           or any(character not in "0123456789abcdef" for character in value[7:])
                           for value in (self.instrument_identity_key, self.firmware_fingerprint))):
                raise ValueError("instrument trace admission requires identity/points without I/Q geometry")
        elif (self.sample_rate_hz is None or not isfinite(self.sample_rate_hz)
              or self.sample_rate_hz <= 0
              or type(self.fft_size) is not int or self.fft_size < 2
              or (self.mode is CaptureMeasurementMode.RTBW and (
                  type(self.hop_size) is not int or not 1 <= self.hop_size <= self.fft_size))
              or (self.mode is CaptureMeasurementMode.SWEEP and self.hop_size is not None
                  and (type(self.hop_size) is not int or not 1 <= self.hop_size <= self.fft_size))
              or self.trace_points is not None or self.instrument_model_id is not None
              or self.instrument_identity_key is not None
              or self.firmware_fingerprint is not None):
            raise ValueError("I/Q capture admission requires applied Fs/FFT/hop without trace identity")
        if self.mode is CaptureMeasurementMode.RTBW and (self.session_id is None or not self.session_id):
            raise ValueError("RTBW admission requires a producer session")
        if self.mode is not CaptureMeasurementMode.RTBW and self.session_id is not None and self.paired_sweep_run is None:
            raise ValueError("Sweep admission cannot claim an RTBW session")
        if self.paired_sweep_run is not None:
            run = self.paired_sweep_run
            if (not isinstance(run, PairedSweepRunIdentity)
                    or self.mode is not CaptureMeasurementMode.SWEEP
                    or self.session_id != run.request.pair.session_id
                    or self.acquisition_epoch != run.acquisition_epoch
                    or self.config_generation is not None or self.hop_size is not None
                    or self.source_id != run.request.pair.device_id or self.unit != "dBFS/bin"
                    or self.sample_rate_hz != run.request.pair.configuration.sample_rate_hz
                    or self.fft_size != run.request.pair.configuration.fft_size
                    or set(endpoints) != {run.request.pair.primary_source_id, run.request.pair.secondary_source_id}
                    or bindings != tuple((item, item) for item in endpoints)):
                raise ValueError("paired Sweep admission must retain BOTH exact producers and admitted run")

    def producer_source_id(self, endpoint_id: str) -> str:
        if endpoint_id not in self.receiver_endpoint_ids:
            raise ValueError("producer source requires an admitted endpoint")
        return dict(self.endpoint_source_ids).get(endpoint_id, self.source_id)


class PaneCaptureOwner(Protocol):
    """One externally constructed, inert adapter for exactly one RX resource.

    ``start_capture`` returns a receipt only after confirming applied mode,
    source and geometry. It may partially acquire a device before raising. The
    session then retains the owner/lease until an explicit Stop succeeds.
    ``stop_capture_and_wait`` must confirm terminal release or raise.
    Recording and receiver-identity queries are inert read-only checks.
    ``control_transaction`` is a reentrant exclusion shared with every
    recording Start/attach and RX transition on this owner. Merely querying
    recording state before Stop/retune is not an atomic recording guarantee.
    """

    physical_stream_resource_id: str

    def validate_endpoint(self, endpoint: ReceiverEndpoint | SpectrumTraceEndpoint) -> None: ...
    def validate_job(self, job: CaptureJob) -> None: ...
    def release_control_claim(self) -> None: ...
    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission: ...
    def stop_capture_and_wait(self) -> None: ...
    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]: ...
    def recording_active(self) -> bool: ...
    def recording_conflict(self, job: CaptureJob) -> bool: ...
    def receiver_identity(self, endpoint_id: str) -> str | int | None: ...
    def control_transaction(self) -> ContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class PaneResourcePreview:
    physical_stream_resource_id: str
    affected_pane_ids: tuple[str, ...]
    pane_assignments: tuple[tuple[str, str], ...]
    revisit_estimates: tuple[PaneRevisitEstimate, ...]
    capture_job_count: int
    planned_cycle_s: float
    planned_control_boundaries_per_cycle: int
    recording_conflict: bool


@dataclass(frozen=True, slots=True)
class PaneActivation:
    """One exact active publication token; host elapsed is not ADC/RF loss."""

    physical_stream_resource_id: str
    capture_id: str
    slot_index: int
    host_activation_serial: int
    planned_control_gap: PaneControlGap | None
    host_control_elapsed_s: float | None


@dataclass(frozen=True, slots=True)
class PaneDelivery:
    """Same immutable measurement, with a read-only crop intent per pane."""

    physical_stream_resource_id: str
    capture_id: str
    receiver_endpoint_id: str
    host_activation_serial: int
    crop: PaneCrop
    bundle: AnalyzerFrameBundle
    host_received_monotonic_s: float
    host_run_serial: int = 0

    @property
    def pane_id(self) -> str:
        return self.crop.pane_id


@dataclass(frozen=True, slots=True)
class PaneHostTiming:
    """Host-observed data age and last interval between accepted visits.

    A visit begins with the first accepted frame from a new capture activation.
    Further FFT or partial Sweep publications in that activation are not new
    visits. Neither value is an RF duty, ADC, paint or pulse-detection metric.
    """

    frame_age_s: float | None
    last_revisit_s: float | None


@dataclass(frozen=True, slots=True)
class PaneResourcePlanPreview:
    """An inert range-change proposal anchored to one exact plan and run.

    All panes on this RX are affected, including unchanged shared/time-sliced
    neighbors. Applying requires a separately confirmed full Stop. A preview
    is not a capability receipt, hardware configuration or Start permission.
    """

    physical_stream_resource_id: str
    expected_schedule: PaneSchedule
    proposed_schedule: PaneSchedule
    expected_run_serial: int
    affected_pane_ids: tuple[str, ...]
    restart_required: bool

    def __post_init__(self) -> None:
        if (not isinstance(self.expected_schedule, PaneSchedule)
                or not isinstance(self.proposed_schedule, PaneSchedule)
                or not isinstance(self.physical_stream_resource_id, str)
                or type(self.expected_run_serial) is not int or self.expected_run_serial < 0
                or type(self.restart_required) is not bool):
            raise PaneResourceError("RF preview requires exact typed schedules and run state")
        resource = next((item for item in self.expected_schedule.resources
                         if item.physical_stream_resource_id == self.physical_stream_resource_id), None)
        try:
            affected = tuple(self.affected_pane_ids)
        except TypeError:
            raise PaneResourceError("RF preview requires the complete affected pane set") from None
        expected = (() if resource is None else
                    tuple(sorted({crop.pane_id for job in resource.jobs for crop in job.crops})))
        if not expected or affected != expected:
            raise PaneResourceError("RF preview requires the complete affected pane set")
        object.__setattr__(self, "affected_pane_ids", affected)


@dataclass(slots=True)
class _Runtime:
    group: AcquisitionGroup
    schedule: ResourcePaneSchedule
    owner: PaneCaptureOwner
    lease: ReceiverLease | None = None
    slot_index: int = -1
    active: bool = False
    stop_required: bool = False
    terminal: bool = False
    activation_serial: int = 0
    run_serial: int = 0
    admission: PaneCaptureAdmission | None = None
    current_activation: PaneActivation | None = None
    last_epoch: int | None = None
    last_session_id: str | None = None
    last_admission_epoch: int | None = None
    last_admission_session_id: str | None = None
    new_epoch_required: bool = False
    rejected_publications: int = 0
    last_paired_sweep_key: tuple[int, int, int, int] | None = None
    last_paired_sweep_run: PairedSweepRunIdentity | None = None
    last_pane_observation: dict[str, tuple[PairedSweepRunIdentity, tuple[int, ...]]] = field(default_factory=dict)
    last_stop_started_s: float | None = None
    pending_control_gap: PaneControlGap | None = None
    pending_control_started_s: float | None = None
    control_lock: LockType = field(default_factory=Lock, repr=False)

    @property
    def current_job(self) -> CaptureJob | None:
        if self.slot_index < 0:
            return None
        capture_id = self.schedule.slots[self.slot_index].capture_id
        return next(job for job in self.schedule.jobs if job.capture_id == capture_id)


class PaneResourceSession:
    """One finite plan, one lease/owner per resource, explicit lifecycle only.

    Planned revisit and control gaps come from the plan. ``pane_age_s`` is a
    different host-observed measure since the last admitted publication; it
    does not estimate ADC duty, pulse-detection probability or RF loss.
    """

    def __init__(
        self,
        schedule: PaneSchedule,
        groups: tuple[AcquisitionGroup, ...],
        owners: Mapping[str, PaneCaptureOwner],
        lease_manager: ReceiverLeaseManager,
        *,
        source_identity_keys: Mapping[str, str | None] | None = None,
        source_families: Mapping[str, DeviceFamily] | None = None,
        owner_factories: Mapping[str, Callable[[], PaneCaptureOwner]] | None = None,
        now_s: Callable[[], float] = monotonic,
    ) -> None:
        resource_ids = {item.physical_stream_resource_id for item in schedule.resources}
        group_ids = {item.physical_stream_resource_id for item in groups}
        if len(group_ids) != len(groups) or resource_ids != group_ids or resource_ids != set(owners):
            raise PaneResourceError("plan, acquisition groups and owners must name the same unique resources")
        endpoints: set[str] = set()
        source_ids: set[str] = set()
        pane_ids: set[str] = set()
        runtimes: dict[str, _Runtime] = {}
        pane_resources: dict[str, str] = {}
        expected_receivers: dict[str, str | int | None] = {}
        for group in groups:
            resource_id = group.physical_stream_resource_id
            owner = owners[resource_id]
            if owner.physical_stream_resource_id != resource_id:
                raise PaneResourceError("owner physical resource identity differs from the admitted plan")
            if any(not callable(getattr(owner, name, None)) for name in (
                    "validate_endpoint", "validate_job", "release_control_claim", "start_capture",
                    "stop_capture_and_wait",
                    "poll_bundles", "recording_active",
                    "recording_conflict", "receiver_identity", "control_transaction")):
                raise PaneResourceError("receiver owner lacks a required lifecycle or recording guard")
            if any(isinstance(endpoint, ReceiverEndpoint)
                   and endpoint.selection is ReceiverChainSelection.BOTH for endpoint in group.endpoints):
                raise PaneResourceError("one spectrum bundle cannot represent both digital RX channels")
            group_sources = {endpoint.source_id for endpoint in group.endpoints}
            if len(group_sources) != 1 or source_ids.intersection(group_sources):
                raise PaneResourceError("each physical resource requires one distinct operational source")
            source_ids.update(group_sources)
            by_endpoint = {endpoint.endpoint_id: endpoint for endpoint in group.endpoints}
            planned = next(item for item in schedule.resources if item.physical_stream_resource_id == resource_id)
            trace_endpoint = isinstance(group.endpoints[0], SpectrumTraceEndpoint)
            if any(isinstance(job.profile, SpectrumTracePaneProfile) != trace_endpoint for job in planned.jobs):
                raise PaneResourceError("instrument trace and I/Q receiver jobs cannot share an endpoint type")
            if (len(by_endpoint) > 1 and any(job.profile.measurement_mode is CaptureMeasurementMode.SWEEP
                                             and not isinstance(job.profile, Ad936xPairedSweepPaneProfile)
                                             for job in planned.jobs)):
                raise PaneResourceError("Sweep producer cannot identify two RX channels in one resource")
            if endpoints.intersection(by_endpoint):
                raise PaneResourceError("receiver endpoint identity is duplicated between resources")
            endpoints.update(by_endpoint)
            try:
                for endpoint in group.endpoints:
                    owner.validate_endpoint(endpoint)
                mapped = {endpoint_id: owner.receiver_identity(endpoint_id) for endpoint_id in by_endpoint}
            except Exception:
                raise PaneResourceError("producer receiver identity could not be confirmed") from None
            if (any(job.profile.measurement_mode is not CaptureMeasurementMode.RTBW
                    and not isinstance(job.profile, Ad936xPairedSweepPaneProfile) for job in planned.jobs)
                    and any(value is not None for value in mapped.values())):
                raise PaneResourceError("Sweep publication has no producer RX identity")
            known = [value for value in mapped.values() if value is not None]
            if (any(type(value) not in {str, int} or value == "" for value in known)
                    or (len(by_endpoint) > 1 and len(known) != len(by_endpoint))
                    or len(set(known)) != len(known)):
                raise PaneResourceError("multi-RX endpoints need distinct observed producer receiver identities")
            expected_receivers.update(mapped)
            for job in planned.jobs:
                if job.physical_stream_resource_id != resource_id or not set(job.receiver_endpoint_ids) <= set(by_endpoint):
                    raise PaneResourceError("capture job crosses its admitted physical resource")
                try:
                    owner.validate_job(job)
                except Exception:
                    raise PaneResourceError("receiver owner refuses a scheduled capture job") from None
                for crop in job.crops:
                    if crop.receiver_endpoint_id not in by_endpoint or crop.pane_id in pane_ids:
                        raise PaneResourceError("pane crop has a foreign endpoint or duplicate pane identity")
                    pane_ids.add(crop.pane_id)
                    pane_resources[crop.pane_id] = resource_id
            runtimes[resource_id] = _Runtime(group, planned, owner)
        if pane_ids != {item.pane_id for item in schedule.pane_revisits}:
            raise PaneResourceError("pane delivery and planned revisit identities differ")
        # Operational IDs are routes scoped to an adapter. USB and IP entries
        # for the SAME physical receiver can have different source IDs. Within
        # one family, independent resources therefore need distinct observed
        # canonical identities. An unidentifiable source is safe only when its
        # selected device family occurs exactly once in this plan: one physical
        # device cannot simultaneously be an AD936x, HackRF and tinySA.
        if len(runtimes) > 1:
            identities = dict(source_identity_keys or {})
            if identities.keys() != source_ids or any(
                    key is not None and (not isinstance(key, str) or not key.startswith("sha256:")
                    or len(key) != 71 or any(character not in "0123456789abcdef" for character in key[7:])
                    ) for key in identities.values()):
                raise PaneResourceError("parallel receivers require exact canonical source identities")
            known_keys = tuple(key for key in identities.values() if key is not None)
            if len(set(known_keys)) != len(known_keys):
                raise PaneResourceError("two operational sources alias one physical receiver")
            if any(key is None for key in identities.values()):
                families = dict(source_families or {})
                if (families.keys() != source_ids
                        or any(not isinstance(family, DeviceFamily) for family in families.values())
                        or any(sum(other is families[source_id] for other in families.values()) != 1
                               for source_id, key in identities.items() if key is None)):
                    raise PaneResourceError("unidentified parallel receiver needs a unique selected family")
        factories = dict(owner_factories or {})
        if not set(factories) <= resource_ids or any(not callable(factory) for factory in factories.values()):
            raise PaneResourceError("fresh pane owners require exact known resource factories")
        self._owner_factories = factories
        self._runtimes = runtimes
        self._schedule = schedule
        self._pane_resources = pane_resources
        self._endpoints = {endpoint.endpoint_id: endpoint for group in groups for endpoint in group.endpoints}
        self._expected_receivers = expected_receivers
        self._leases = lease_manager
        self._now_s = now_s
        self._apply_lock = Lock()
        self._state_lock = RLock()
        self._pane_last_received: dict[str, float] = {}
        self._pane_last_visit_activation: dict[str, PaneActivation] = {}
        self._pane_last_visit_received: dict[str, float] = {}
        self._pane_last_revisit_s: dict[str, float] = {}
        self._applied = False
        self._retired = False

    @property
    def active_resource_count(self) -> int:
        with self._state_lock:
            return sum(runtime.active for runtime in self._runtimes.values())

    @property
    def schedule(self) -> PaneSchedule:
        """Exact immutable plan whose resources this session owns."""
        return self._schedule

    @property
    def retained_resource_count(self) -> int:
        with self._state_lock:
            return sum(runtime.lease is not None for runtime in self._runtimes.values())

    def preview(self) -> tuple[PaneResourcePreview, ...]:
        """Expose all neighboring-pane and recording effects before Apply."""
        result: list[PaneResourcePreview] = []
        for resource_id, runtime in sorted(self._runtimes.items()):
            schedule = runtime.schedule
            affected = tuple(sorted({crop.pane_id for job in schedule.jobs for crop in job.crops}))
            assignments = tuple(sorted((crop.pane_id, crop.receiver_endpoint_id)
                                       for job in schedule.jobs for crop in job.crops))
            revisits = tuple(item for item in self._schedule.pane_revisits
                             if item.physical_stream_resource_id == resource_id)
            boundaries = sum(slot.control_gap_before is not None for slot in schedule.slots)
            boundaries += int(schedule.cycle_transition_gap is not None)
            result.append(PaneResourcePreview(
                resource_id, affected, assignments, revisits,
                len(schedule.jobs), schedule.cycle_duration_s,
                boundaries, self._recording_conflict(runtime),
            ))
        return tuple(result)

    def apply(self) -> tuple[PaneResourcePreview, ...]:
        """Reserve exact resource keys, without Start, retune or hidden retry."""
        with self._apply_lock:
            with self._state_lock:
                if self._applied or self._retired or any(runtime.terminal for runtime in self._runtimes.values()):
                    raise PaneResourceError("pane plan has already been applied or terminated")
            preview = self.preview()
            if any(item.recording_conflict for item in preview):
                raise PaneResourceError("recording conflicts with the proposed capture or retune plan")
            with self._state_lock:
                acquired: list[_Runtime] = []
                try:
                    for resource_id in sorted(self._runtimes):
                        runtime = self._runtimes[resource_id]
                        runtime.lease = self._leases.acquire(runtime.group)
                        acquired.append(runtime)
                except Exception:
                    for runtime in reversed(acquired):
                        assert runtime.lease is not None
                        runtime.lease.release()
                        runtime.lease = None
                    raise PaneResourceError("physical receiver resource lease could not be acquired") from None
                self._applied = True
                return preview

    def can_rearm_resource(self, resource_id: str) -> bool:
        """Small state-only eligibility query; no owner creation, lease or I/O."""
        with self._state_lock:
            runtime = self._required_runtime(resource_id)
            return bool(self._applied and not self._retired and resource_id in self._owner_factories
                        and runtime.terminal and runtime.lease is None
                        and not runtime.active and not runtime.stop_required
                        and runtime.admission is None and runtime.current_activation is None)

    def _validate_resource_plan(self, runtime: _Runtime, proposed: PaneSchedule) -> ResourcePaneSchedule:
        """No new resource, endpoint, pane route or peer plan can be smuggled in."""
        if not isinstance(proposed, PaneSchedule):
            raise PaneResourceError("receiver range change requires a compiled pane schedule")
        current = {item.physical_stream_resource_id: item for item in self._schedule.resources}
        incoming = {item.physical_stream_resource_id: item for item in proposed.resources}
        resource_id = runtime.group.physical_stream_resource_id
        if (incoming.keys() != current.keys() or incoming[resource_id] == runtime.schedule
                or any(incoming[key] != value for key, value in current.items() if key != resource_id)):
            raise PaneResourceError("receiver range change must affect exactly one existing resource")
        before = {crop.pane_id: (crop.receiver_endpoint_id, job.profile.measurement_mode, job.profile.unit)
                  for job in runtime.schedule.jobs for crop in job.crops}
        following = incoming[resource_id]
        after = {crop.pane_id: (crop.receiver_endpoint_id, job.profile.measurement_mode, job.profile.unit)
                 for job in following.jobs for crop in job.crops}
        expected_revisits = {item.pane_id: item for item in self._schedule.pane_revisits}
        new_revisits = {item.pane_id: item for item in proposed.pane_revisits}
        if (before != after or expected_revisits.keys() != new_revisits.keys()
                or any(new_revisits[key] != value for key, value in expected_revisits.items() if key not in before)
                or any(item.physical_stream_resource_id != resource_id
                       for key, item in new_revisits.items() if key in before)
                or any(item.requested_maximum_revisit_s is not None
                       and item.maximum_revisit_s > item.requested_maximum_revisit_s
                       and not isclose(item.maximum_revisit_s, item.requested_maximum_revisit_s,
                                       rel_tol=1e-12, abs_tol=1e-12) for item in proposed.pane_revisits)):
            raise PaneResourceError("receiver range change alters pane routes, modes, units or revisit admission")
        endpoints = {endpoint.endpoint_id for endpoint in runtime.group.endpoints}
        if any(not set(job.receiver_endpoint_ids) <= endpoints for job in following.jobs):
            raise PaneResourceError("receiver range change has a foreign RX endpoint")
        try:
            for job in following.jobs:
                runtime.owner.validate_job(job)
            if self._recording_conflict(runtime) or self._recording_conflict(
                    _Runtime(runtime.group, following, runtime.owner)):
                raise PaneResourceError("recording conflicts with receiver range change")
        except PaneResourceError:
            raise
        except Exception:
            raise PaneResourceError("receiver refuses the proposed range plan") from None
        return following

    def preview_resource_plan(self, resource_id: str, proposed: PaneSchedule) -> PaneResourcePlanPreview:
        """Off-Qt, serialized inert preflight; never Stop/apply/Start an owner."""
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock:
            with self._state_lock:
                if not self._applied or self._retired or runtime.stop_required:
                    raise PaneResourceError("receiver is unavailable for range preview")
            # A stopped adapter has released its application claim. Preview
            # must not reclaim it and strand the normal fresh-adapter Start.
            with self._owner_control_transaction(runtime) if runtime.lease is not None else nullcontext():
                self._validate_resource_plan(runtime, proposed)
                with self._state_lock:
                    return PaneResourcePlanPreview(resource_id, self._schedule, proposed,
                        runtime.run_serial, self.stop_impact(next(
                            crop.pane_id for job in runtime.schedule.jobs for crop in job.crops)), runtime.active)

    def replace_stopped_resource_plan(self, preview: PaneResourcePlanPreview) -> None:
        """Apply host routing ONLY after full release; next Start stays explicit.

        The same graph/factory/lease manager remains authoritative. Timeslice
        advances do not stale a preview, but a new run or any accepted plan
        does. The actual last capture, not a preview-time slot, defines the gap.
        """
        if not isinstance(preview, PaneResourcePlanPreview):
            raise PaneResourceError("receiver range Apply needs its exact preview")
        runtime = self._required_runtime(preview.physical_stream_resource_id)
        with runtime.control_lock, self._apply_lock:
            with self._state_lock:
                if (self._schedule is not preview.expected_schedule
                        or runtime.run_serial != preview.expected_run_serial
                        or not self.can_rearm_resource(preview.physical_stream_resource_id)):
                    raise PaneResourceError("range preview is stale or receiver Stop has not fully released")
            # Pure host commit after full release. The next explicit Start
            # rechecks recording under the fresh owner's native transaction.
            following = self._validate_resource_plan(runtime, preview.proposed_schedule)
            previous = runtime.current_job
            first = next(job for job in following.jobs if job.capture_id == following.slots[0].capture_id)
            previous_capture = (previous.capture_id if previous is not None else
                                runtime.pending_control_gap.previous_capture_id if runtime.pending_control_gap is not None
                                else None)
            gap = (None if previous_capture is None or runtime.last_admission_epoch is None else PaneControlGap(
                preview.physical_stream_resource_id, previous_capture, first.capture_id,
                PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE, first.profile.epoch_cost.control_gap_s))
            with self._state_lock:
                runtime.schedule = following
                runtime.slot_index = -1
                runtime.pending_control_gap = gap
                runtime.pending_control_started_s = runtime.last_stop_started_s if gap is not None else None
                self._schedule = preview.proposed_schedule

    def stop_for_resource_plan(self, preview: PaneResourcePlanPreview) -> None:
        """Explicit approved RF Stop, with stale/recording refusal BEFORE Stop.

        This is NOT the ordinary emergency/selected Stop: recording does not
        block those. Approval cannot stop a newer run or race recorder attach
        between the check and Stop under this owner's native transaction.
        """
        if not isinstance(preview, PaneResourcePlanPreview):
            raise PaneResourceError("RF Stop requires an exact approved impact preview")
        runtime = self._required_runtime(preview.physical_stream_resource_id)
        with runtime.control_lock:
            with self._state_lock:
                if (self._schedule is not preview.expected_schedule
                        or runtime.run_serial != preview.expected_run_serial
                        or not self._applied or self._retired or runtime.stop_required):
                    raise PaneResourceError("RF preview is stale; no receiver Stop was sent")
            with self._owner_control_transaction(runtime) if runtime.lease is not None else nullcontext():
                self._validate_resource_plan(runtime, preview.proposed_schedule)
                if runtime.lease is not None:
                    self._stop_runtime_locked(runtime)

    def rearm_resource(self, resource_id: str) -> None:
        """Explicit next-run preparation, off Qt, after confirmed full Stop.

        A product factory constructs an inert control adapter over the SAME
        selected application graph. It must not discover/open an SDK. The
        family's normal Start still issues its own fresh single-use permit;
        this resource lease is not that permit. Healthy peer owners, leases
        and admissions are not replaced or polled here.
        """
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock, self._apply_lock:
            if not self.can_rearm_resource(resource_id):
                raise PaneResourceError("receiver has not fully released for an explicit new run")
            try:
                owner = self._owner_factories[resource_id]()
                if (owner is runtime.owner
                        or any(owner is peer.owner for peer in self._runtimes.values())
                        or owner.physical_stream_resource_id != resource_id
                        or any(not callable(getattr(owner, name, None)) for name in (
                            "validate_endpoint", "validate_job", "release_control_claim", "start_capture",
                            "stop_capture_and_wait", "poll_bundles", "recording_active",
                            "recording_conflict", "receiver_identity", "control_transaction"))):
                    raise ValueError("fresh receiver adapter differs from the admitted resource")
                for endpoint in runtime.group.endpoints:
                    owner.validate_endpoint(endpoint)
                    observed = owner.receiver_identity(endpoint.endpoint_id)
                    expected = self._expected_receivers[endpoint.endpoint_id]
                    if type(observed) is not type(expected) or observed != expected:
                        raise ValueError("fresh receiver adapter changed producer RX identity")
                for job in runtime.schedule.jobs:
                    owner.validate_job(job)
                if self._recording_conflict(_Runtime(runtime.group, runtime.schedule, owner)):
                    raise ValueError("recording conflicts with the explicit new run")
            except Exception as error:
                raise PaneResourceError("fresh receiver adapter did not confirm the existing plan",
                    failure=pane_failure_from_exception(error, PaneFailureStage.REARM)) from None
            try:
                lease = self._leases.acquire(runtime.group)
            except Exception as error:
                raise PaneResourceError("physical receiver lease is unavailable for the new run",
                    failure=pane_failure_from_exception(error, PaneFailureStage.REARM)) from None
            with self._state_lock:
                runtime.owner = owner
                runtime.lease = lease
                runtime.slot_index = -1
                runtime.terminal = False
                runtime.new_epoch_required = runtime.last_admission_epoch is not None
                for pane_id, pane_resource in self._pane_resources.items():
                    if pane_resource == resource_id:
                        runtime.last_pane_observation.pop(pane_id, None)
                        self._pane_last_received.pop(pane_id, None)
                        self._pane_last_visit_activation.pop(pane_id, None)
                        self._pane_last_visit_received.pop(pane_id, None)
                        self._pane_last_revisit_s.pop(pane_id, None)

    def retire_after_stop(self) -> None:
        """Seal new-run factories before terminal application graph close."""
        with self._apply_lock, self._state_lock:
            if any(runtime.lease is not None or runtime.active or runtime.stop_required
                   for runtime in self._runtimes.values()):
                raise PaneResourceError("receiver Stop must release every resource before retirement")
            self._retired = True
            self._owner_factories.clear()

    def start_resource(self, resource_id: str) -> PaneActivation:
        """One explicit Start; partial owner failure retains its lease for Stop."""
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock:
            with self._state_lock:
                if (not self._applied or runtime.lease is None or runtime.active
                        or runtime.stop_required or runtime.terminal or runtime.slot_index >= 0):
                    raise PaneResourceError("receiver is not in the applied, not-started state")
            with self._owner_control_transaction(runtime):
                if self._recording_conflict(runtime):
                    raise PaneResourceError("recording conflicts with receiver Start")
                with self._state_lock:
                    runtime.slot_index = 0
                    job = runtime.current_job
                    assert job is not None
                    runtime.activation_serial += 1
                    runtime.run_serial += 1
                    runtime.stop_required = True  # Reject frames until Start confirms.
                control_started = self._clock_sample_s()
                failure_stage = PaneFailureStage.START
                try:
                    admission = runtime.owner.start_capture(job)
                    failure_stage = PaneFailureStage.ADMISSION
                    self._validate_admission(runtime, job, admission)
                except Exception as error:
                    raise PaneResourceError("receiver Start did not confirm admission; explicit Stop is required",
                        failure=pane_failure_from_exception(error, failure_stage,
                            reason=(PaneFailureReason.INVALID_ADMISSION if failure_stage is PaneFailureStage.ADMISSION
                                    else PaneFailureReason.OPERATION_FAILED))) from None
                control_elapsed = self._elapsed_since(
                    runtime.pending_control_started_s if runtime.pending_control_gap is not None else control_started)
                with self._state_lock:
                    activation = PaneActivation(resource_id, job.capture_id, 0, runtime.activation_serial,
                                                runtime.pending_control_gap or runtime.schedule.initial_control_gap, control_elapsed)
                    runtime.pending_control_gap = None
                    runtime.pending_control_started_s = None
                    runtime.admission = admission
                    runtime.last_admission_epoch = admission.acquisition_epoch
                    runtime.last_admission_session_id = admission.session_id
                    runtime.current_activation = activation
                    runtime.active = True
                    runtime.stop_required = False
                    return activation

    def advance_resource(self, resource_id: str) -> PaneActivation:
        """Advance a pre-accepted finite slot; no retune before confirmed Stop."""
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock:
            with self._owner_control_transaction(runtime):
                with self._state_lock:
                    if not runtime.active or runtime.stop_required or runtime.lease is None:
                        raise PaneResourceError("receiver has no active capture to advance")
                    next_index = (runtime.slot_index + 1) % len(runtime.schedule.slots)
                    gap = (runtime.schedule.cycle_transition_gap if next_index == 0 else
                           runtime.schedule.slots[next_index].control_gap_before)
                    next_id = runtime.schedule.slots[next_index].capture_id
                    next_job = next(job for job in runtime.schedule.jobs if job.capture_id == next_id)
                    if gap is None:
                        current = runtime.current_job
                        if (current is None or current.configuration_key != next_job.configuration_key
                                or runtime.admission is None):
                            raise PaneResourceError("gapless slot cannot change the admitted receiver configuration")
                        runtime.slot_index = next_index
                        runtime.admission = replace(runtime.admission, capture_id=next_id)
                        activation = PaneActivation(resource_id, next_id, next_index,
                                                    runtime.activation_serial, None, None)
                        runtime.current_activation = activation
                        return activation
                if self._recording_conflict(runtime):
                    raise PaneResourceError("recording blocks a scheduled receiver retune")
                control_started = self._clock_sample_s()
                with self._state_lock:
                    runtime.active = False
                    runtime.current_activation = None
                    runtime.stop_required = True  # Close routing before blocking Stop.
                try:
                    runtime.owner.stop_capture_and_wait()
                except Exception as error:
                    raise PaneResourceError("receiver Stop did not confirm release; no retune was sent",
                        failure=pane_failure_from_exception(error, PaneFailureStage.STOP)) from None
                with self._state_lock:
                    runtime.new_epoch_required = runtime.last_admission_epoch is not None
                    runtime.admission = None
                    runtime.slot_index = next_index
                    runtime.activation_serial += 1
                failure_stage = PaneFailureStage.START
                try:
                    admission = runtime.owner.start_capture(next_job)
                    failure_stage = PaneFailureStage.ADMISSION
                    self._validate_admission(runtime, next_job, admission)
                except Exception as error:
                    raise PaneResourceError("next receiver Start failed; owner retained for explicit Stop",
                        failure=pane_failure_from_exception(error, failure_stage,
                            reason=(PaneFailureReason.INVALID_ADMISSION if failure_stage is PaneFailureStage.ADMISSION
                                    else PaneFailureReason.OPERATION_FAILED))) from None
                control_elapsed = self._elapsed_since(control_started)
                with self._state_lock:
                    activation = PaneActivation(resource_id, next_id, next_index,
                                                runtime.activation_serial, gap, control_elapsed)
                    runtime.admission = admission
                    runtime.last_admission_epoch = admission.acquisition_epoch
                    runtime.last_admission_session_id = admission.session_id
                    runtime.current_activation = activation
                    runtime.active = True
                    runtime.stop_required = False
                    return activation

    def accept_frame(self, activation: PaneActivation, endpoint_id: str,
                     bundle: AnalyzerFrameBundle) -> tuple[PaneDelivery, ...]:
        """Only the exact current activation may route a producer publication."""
        return self._accept_frame(activation, endpoint_id, bundle)

    def _accept_frame(self, activation: PaneActivation, endpoint_id: str,
                      bundle: AnalyzerFrameBundle, *, prepare_pair: bool = False,
                      received_s: float | None = None) -> tuple[PaneDelivery, ...]:
        if not isinstance(activation, PaneActivation):
            raise PaneResourceError("receiver publication requires its confirmed activation")
        resource_id = activation.physical_stream_resource_id
        capture_id = activation.capture_id
        with self._state_lock:
            runtime = self._required_runtime(resource_id)
            job = runtime.current_job
            admission = runtime.admission
            endpoint = self._endpoints.get(endpoint_id)
            identity = getattr(bundle, "identity", None)
            if (not runtime.active or runtime.stop_required or job is None or admission is None
                    or (admission.paired_sweep_run is not None and not prepare_pair)
                    or (isinstance(bundle, AnalyzerFrameBundle) and bundle.paired_sweep is not None
                        and admission.paired_sweep_run is None)
                    or runtime.current_activation is not activation
                    or job.capture_id != capture_id or admission.capture_id != capture_id
                    or endpoint is None or endpoint_id not in job.receiver_endpoint_ids
                    or endpoint.physical_stream_resource_id != resource_id
                    or not isinstance(bundle, AnalyzerFrameBundle) or identity is None
                    or identity.source_id != admission.producer_source_id(endpoint_id)
                    or (admission.mode is CaptureMeasurementMode.INSTRUMENT_TRACE
                        and (bundle.mode != "sweep" or not isinstance(bundle.spectrum, SweepLineFrame)
                             or bundle.spectrum.instrument is None
                             or bundle.spectrum.instrument.points != admission.trace_points
                             or bundle.spectrum.instrument.model_id != admission.instrument_model_id
                             or bundle.spectrum.instrument.device_identity_key != admission.instrument_identity_key
                             or bundle.spectrum.instrument.firmware_fingerprint != admission.firmware_fingerprint))
                    or (admission.mode is not CaptureMeasurementMode.INSTRUMENT_TRACE
                        and bundle.mode != admission.mode)
                    or identity.unit != admission.unit
                    or (admission.session_id is not None and identity.session_id != admission.session_id)
                    or (admission.config_generation is not None
                        and identity.config_generation != admission.config_generation)
                    or (admission.mode is CaptureMeasurementMode.RTBW and (bundle.rtbw is None
                        or bundle.rtbw.sample_rate_hz != admission.sample_rate_hz
                        or bundle.rtbw.fft_size != admission.fft_size
                        or bundle.rtbw.hop_size != admission.hop_size))
                    or type(identity.acquisition_epoch) is not int
                    or identity.acquisition_epoch != admission.acquisition_epoch
                    or identity.receiver_id != self._expected_receivers[endpoint_id]):
                runtime.rejected_publications += 1
                return ()
            epoch = identity.acquisition_epoch
            previous = runtime.last_epoch
            same_session = identity.session_id == runtime.last_session_id
            if ((runtime.new_epoch_required and same_session and epoch == previous)
                    or (same_session and isinstance(epoch, int) and isinstance(previous, int)
                        and epoch < previous)):
                runtime.rejected_publications += 1
                return ()
            grid = bundle.frequencies_hz
            # Instrument Stop is the exclusive right edge of its reported
            # sweep; its final point center is normally one step before Stop.
            # A validated full RTBW FFT also has an exclusive next-bin edge;
            # the final bin CENTER is not the positive Nyquist boundary.
            # Sweep progress keeps its existing last-center coverage.
            trace = (bundle.spectrum.instrument if admission.mode is CaptureMeasurementMode.INSTRUMENT_TRACE
                     and isinstance(bundle.spectrum, SweepLineFrame) else None)
            lower = float(trace.start_hz) if trace is not None else float(grid[0]) if len(grid) else 0.0
            upper = float(trace.stop_hz) if trace is not None else float(grid[-1]) if len(grid) else 0.0
            if admission.mode is CaptureMeasurementMode.RTBW:
                bounds = bundle.rtbw_frequency_bounds_hz
                assert bounds is not None  # exact RTBW metadata guarded above
                lower, upper = bounds
            if (len(grid) < 2 or any(crop.start_hz < lower or crop.stop_hz > upper
                                     for crop in job.crops if crop.receiver_endpoint_id == endpoint_id)):
                runtime.rejected_publications += 1
                return ()
            now = self._clock_sample_s() if received_s is None else received_s
            if now is None:
                runtime.rejected_publications += 1
                return ()
            deliveries = tuple(PaneDelivery(resource_id, capture_id, endpoint_id,
                                           runtime.activation_serial, crop, bundle, now, runtime.run_serial)
                               for crop in job.crops if crop.receiver_endpoint_id == endpoint_id)
            if not prepare_pair:
                self._commit_deliveries(runtime, activation, admission, deliveries, now)
            return deliveries

    def _commit_deliveries(self, runtime: _Runtime, activation: PaneActivation,
                          admission: PaneCaptureAdmission, deliveries: tuple[PaneDelivery, ...],
                          now: float) -> None:
        runtime.last_epoch = admission.acquisition_epoch
        runtime.last_session_id = admission.session_id
        runtime.new_epoch_required = False
        for delivery in deliveries:
            if admission.paired_sweep_run is not None:
                frame = delivery.bundle.spectrum
                assert isinstance(frame, (SweepLineFrame, SweepProgressFrame))
                begin = int(np.searchsorted(frame.frequencies_hz, delivery.crop.start_hz))
                end = int(np.searchsorted(frame.frequencies_hz, delivery.crop.stop_hz, side="right"))
                pair = delivery.bundle.paired_sweep
                assert pair is not None
                # Bin ownership names the FIRST contributor in an overlap, not
                # the latest observed acquisition. Freshness is a host-observed
                # visit to a measured crop, not a per-bin RF timestamp or proof
                # that every value changed. Find the newest observed usable
                # window containing a measured target bin in this crop.
                latest_segment = -1
                for segment in range(len(pair.steps) - 1, -1, -1):
                    identity = pair.steps[segment].primary.identity
                    step_begin = max(begin, int(np.searchsorted(frame.frequencies_hz, identity.usable_start_hz)))
                    step_end = min(end, int(np.searchsorted(frame.frequencies_hz, identity.usable_stop_hz, side="right")))
                    if any(bool(np.any(frame.source_segment_indices[offset:min(offset + 65536, step_end)] >= 0))
                           for offset in range(step_begin, step_end, 65536)):
                        latest_segment = segment
                        break
                if latest_segment < 0:
                    # Still deliver explicit pending/gap masks to the display,
                    # but never count unmeasured bins as a fresh acquired visit.
                    continue
                step = pair.steps[latest_segment]
                observation = step.primary if delivery.bundle.receiver_id == "RX1" else step.secondary
                identity = observation.identity
                receipt = (identity.sweep_epoch, identity.line_sequence, latest_segment,
                           identity.config_generation, identity.synchronization_epoch,
                           observation.frame_sequence, observation.first_sample_index)
                previous_receipt = runtime.last_pane_observation.get(delivery.pane_id)
                if (previous_receipt is not None and previous_receipt[0] is pair.run
                        and previous_receipt[1] == receipt):
                    continue  # cumulative redraw, not newly acquired data for this crop
                runtime.last_pane_observation[delivery.pane_id] = (pair.run, receipt)
            pane_id = delivery.pane_id
            previous_frame = self._pane_last_received.get(pane_id)
            if previous_frame is not None and now < previous_frame:
                self._pane_last_visit_activation.pop(pane_id, None)
                self._pane_last_visit_received.pop(pane_id, None)
                self._pane_last_revisit_s.pop(pane_id, None)
            if self._pane_last_visit_activation.get(pane_id) is not activation:
                previous_visit = self._pane_last_visit_received.get(pane_id)
                if previous_visit is not None and now > previous_visit:
                    self._pane_last_revisit_s[pane_id] = now - previous_visit
                else:
                    self._pane_last_revisit_s.pop(pane_id, None)
                self._pane_last_visit_activation[pane_id] = activation
                self._pane_last_visit_received[pane_id] = now
            self._pane_last_received[pane_id] = now

    def accept_paired_sweep(self, activation: PaneActivation,
                            publications: tuple[tuple[str, AnalyzerFrameBundle], ...]) -> tuple[PaneDelivery, ...]:
        """Validate BOTH against the same live receipt before committing any delivery."""
        if not isinstance(activation, PaneActivation):
            raise PaneResourceError("paired Sweep requires an exact current activation")
        with self._state_lock:
            runtime = self._required_runtime(activation.physical_stream_resource_id)
            admission = runtime.admission
            if (admission is None or admission.paired_sweep_run is None
                    or type(publications) is not tuple or len(publications) != 2
                    or any(not isinstance(item, tuple) or len(item) != 2
                           or not isinstance(item[1], AnalyzerFrameBundle) for item in publications)):
                runtime.rejected_publications += 1
                return ()
            run = admission.paired_sweep_run
            pair = publications[0][1].paired_sweep
            if (pair is None or pair.run is not run
                    or publications[1][1].paired_sweep is not pair
                    or tuple(item[0] for item in publications) != (
                        run.request.pair.primary_source_id, run.request.pair.secondary_source_id)):
                runtime.rejected_publications += 1
                return ()
            key = (run.acquisition_epoch, pair.primary.epoch, pair.primary.sequence,
                   pair.primary.revision if isinstance(pair.primary, SweepProgressFrame) else len(pair.steps) + 1)
            prior = runtime.last_paired_sweep_key
            if (runtime.last_paired_sweep_run is run and prior is not None
                    and (key <= prior or key[1] != prior[1])):
                runtime.rejected_publications += 1
                return ()
            now = self._clock_sample_s()
            if now is None:
                runtime.rejected_publications += 1
                return ()
            prepared = tuple(self._accept_frame(activation, endpoint, bundle,
                prepare_pair=True, received_s=now) for endpoint, bundle in publications)
            if any(not items for items in prepared):
                return ()
            delivered = tuple(item for items in prepared for item in items)
            self._commit_deliveries(runtime, activation, admission, delivered, now)
            runtime.last_paired_sweep_key = key
            runtime.last_paired_sweep_run = run
            return delivered

    def poll_resource(self, resource_id: str) -> tuple[PaneDelivery, ...]:
        """Drain one already-owned producer on a worker, bound to its token.

        The resource control lock prevents Stop/retune while reading its
        bounded presentation queue. An old queued measurement can still be
        returned after Start, so ``accept_frame`` enforces producer identity
        and epoch independently of this host-side serialization.
        """
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock:
            with self._state_lock:
                activation = runtime.current_activation
                if not runtime.active or runtime.stop_required or activation is None:
                    return ()
            failure_stage = PaneFailureStage.OWNER_POLL
            try:
                publications = runtime.owner.poll_bundles()
                failure_stage = PaneFailureStage.PUBLICATION_VALIDATION
                if (not isinstance(publications, tuple) or len(publications) > 64
                        or any(not isinstance(item, tuple) or len(item) != 2
                               or not isinstance(item[0], str)
                               or not isinstance(item[1], AnalyzerFrameBundle)
                               for item in publications)):
                    raise ValueError("malformed bounded pane publication batch")
            except Exception as error:
                with self._state_lock:
                    runtime.active = False
                    runtime.current_activation = None
                    runtime.stop_required = True
                raise PaneResourceError("receiver publication failed; explicit Stop is required",
                    failure=pane_failure_from_exception(error, failure_stage,
                        reason=(PaneFailureReason.INVALID_PUBLICATION
                                if failure_stage is PaneFailureStage.PUBLICATION_VALIDATION
                                else PaneFailureReason.OPERATION_FAILED))) from None
            delivered: list[PaneDelivery] = []
            if runtime.admission is not None and runtime.admission.paired_sweep_run is not None:
                if len(publications) % 2:
                    runtime.rejected_publications += 1
                    return ()
                for index in range(0, len(publications), 2):
                    delivered.extend(self.accept_paired_sweep(activation, publications[index:index + 2]))
                return tuple(delivered)
            for endpoint_id, bundle in publications:
                delivered.extend(self.accept_frame(activation, endpoint_id, bundle))
            return tuple(delivered)

    def pane_age_s(self, pane_id: str) -> float | None:
        return self.pane_host_timing(pane_id).frame_age_s

    def pane_host_timing(self, pane_id: str) -> PaneHostTiming:
        """One bounded host-clock snapshot; no UI-paint or RF continuity claim."""
        with self._state_lock:
            resource_id = self._pane_resources.get(pane_id)
            if resource_id is None:
                raise PaneResourceError("unknown pane identity")
            if not self._runtimes[resource_id].active:
                return PaneHostTiming(None, None)
            received = self._pane_last_received.get(pane_id)
            now = self._clock_sample_s()
            if received is None or now is None:
                return PaneHostTiming(None, None)
            if now < received:
                # The next clock reading must not resurrect a pre-reset age
                # or revisit interval merely by advancing past the old time.
                self._pane_last_received.pop(pane_id, None)
                self._pane_last_visit_activation.pop(pane_id, None)
                self._pane_last_visit_received.pop(pane_id, None)
                self._pane_last_revisit_s.pop(pane_id, None)
                return PaneHostTiming(None, None)
            return PaneHostTiming(now - received, self._pane_last_revisit_s.get(pane_id))

    def stop_impact(self, pane_id: str) -> tuple[str, ...]:
        with self._state_lock:
            resource_id = self._pane_resources.get(pane_id)
            if resource_id is None:
                raise PaneResourceError("unknown pane identity")
            runtime = self._runtimes[resource_id]
            return tuple(sorted({crop.pane_id for job in runtime.schedule.jobs for crop in job.crops}))

    def stop_selected(self, pane_id: str, *, acknowledge_shared: bool = False) -> tuple[str, ...]:
        """Stop the whole resource; never silently keep peer panes apparently live."""
        impact = self.stop_impact(pane_id)
        if len(impact) > 1 and not acknowledge_shared:
            raise PaneResourceError("selected pane shares RX; confirm stopping affected panes")
        self.stop_resource(self._pane_resources[pane_id])
        return impact

    def stop_resource(self, resource_id: str) -> None:
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock:
            with self._state_lock:
                if runtime.lease is None:
                    return
            with self._owner_control_transaction(runtime):
                self._stop_runtime_locked(runtime)

    def _stop_runtime_locked(self, runtime: _Runtime) -> None:
        """Caller holds resource control AND the application's native transaction."""
        with self._state_lock:
            needs_stop = runtime.active or runtime.stop_required
            if runtime.active:
                runtime.last_stop_started_s = self._clock_sample_s()
            runtime.active = False  # No late frame may reach a pane during join.
            runtime.current_activation = None
            runtime.stop_required = needs_stop
        if needs_stop:
            try:
                runtime.owner.stop_capture_and_wait()
            except Exception as error:
                raise PaneResourceError("receiver Stop did not confirm release; owner and lease retained",
                    failure=pane_failure_from_exception(error, PaneFailureStage.STOP)) from None
        try:
            runtime.owner.release_control_claim()
        except Exception as error:
            with self._state_lock:
                runtime.stop_required = False  # Hardware Stop succeeded; keep only the claim/lease obligation.
            raise PaneResourceError("receiver control claim did not release; owner and lease retained",
                failure=pane_failure_from_exception(error, PaneFailureStage.CONTROL_RELEASE)) from None
        with self._state_lock:
            assert runtime.lease is not None
            try:
                runtime.lease.release()
            except Exception as error:
                raise PaneResourceError("receiver lease did not release; explicit Stop is required",
                    failure=pane_failure_from_exception(error, PaneFailureStage.LEASE_RELEASE)) from None
            runtime.lease = None
            runtime.admission = None
            runtime.stop_required = False
            runtime.terminal = True

    def stop_all(self) -> tuple[str, ...]:
        """Try every resource once; failed owners remain retained, not retried."""
        failed: list[str] = []
        for resource_id in sorted(self._runtimes):
            try:
                self.stop_resource(resource_id)
            except PaneResourceError:
                failed.append(resource_id)
        return tuple(failed)

    def rejected_publications(self, resource_id: str) -> int:
        with self._state_lock:
            return self._required_runtime(resource_id).rejected_publications

    def admitted_producer_source_id(self, resource_id: str, endpoint_id: str) -> str | None:
        """Read-only current producer binding, never a frame-derived authority.

        The operational source identifies a selected device route. Paired
        native frames identify explicit producers instead. No active receipt
        means no presentation admission, even if a retained frame exists.
        """
        with self._state_lock:
            runtime = self._required_runtime(resource_id)
            admission = runtime.admission
            if (not runtime.active or runtime.stop_required or runtime.current_activation is None
                    or admission is None or endpoint_id not in admission.receiver_endpoint_ids):
                return None
            return admission.producer_source_id(endpoint_id)

    def _recording_conflict(self, runtime: _Runtime) -> bool:
        try:
            # This service creates a *new* capture. Attaching another pane to
            # an already running recording owner needs a separate proven
            # subscription path; it must not issue a second Start here.
            return (runtime.owner.recording_active()
                    or any(runtime.owner.recording_conflict(job) for job in runtime.schedule.jobs))
        except Exception:
            raise PaneResourceError("recording state could not be confirmed") from None

    @contextmanager
    def _owner_control_transaction(self, runtime: _Runtime) -> Iterator[None]:
        try:
            with runtime.owner.control_transaction():
                yield
        except PaneResourceError:
            raise
        except Exception as error:
            with self._state_lock:
                if runtime.lease is not None and runtime.slot_index >= 0:
                    runtime.active = False
                    runtime.current_activation = None
                    runtime.stop_required = True
            raise PaneResourceError("owner control transaction failed; explicit Stop may be required",
                failure=pane_failure_from_exception(error, PaneFailureStage.TRANSACTION)) from None

    def _clock_sample_s(self) -> float | None:
        try:
            value = self._now_s()
            return float(value) if isinstance(value, (int, float)) and isfinite(value) and value >= 0 else None
        except Exception:
            return None

    def _elapsed_since(self, started_s: float | None) -> float | None:
        ended_s = self._clock_sample_s()
        return None if started_s is None or ended_s is None or ended_s < started_s else ended_s - started_s

    @staticmethod
    def _validate_admission(runtime: _Runtime, job: CaptureJob,
                            admission: PaneCaptureAdmission) -> None:
        if (not isinstance(admission, PaneCaptureAdmission)
                or admission.capture_id != job.capture_id
                or admission.source_id != runtime.group.endpoints[0].source_id
                or admission.receiver_endpoint_ids != job.receiver_endpoint_ids
                or admission.mode is not job.profile.measurement_mode
                or admission.unit != job.profile.unit
                or (isinstance(job.profile, Ad936xPairedSweepPaneProfile)
                    and (admission.paired_sweep_run is None
                         or admission.paired_sweep_run.request is not job.profile.paired_request))
                or (not isinstance(job.profile, Ad936xPairedSweepPaneProfile)
                    and admission.paired_sweep_run is not None)
                or (runtime.new_epoch_required and admission.session_id == runtime.last_admission_session_id
                    and runtime.last_admission_epoch is not None
                    and admission.acquisition_epoch <= runtime.last_admission_epoch)
                or (isinstance(job.profile, SpectrumTracePaneProfile)
                    and (admission.trace_points != job.profile.points
                         or admission.sample_rate_hz is not None or admission.fft_size is not None
                         or admission.hop_size is not None))
                or (not isinstance(job.profile, SpectrumTracePaneProfile)
                    and (admission.sample_rate_hz != job.profile.sample_rate_hz
                         or admission.fft_size != job.profile.fft_size
                         or admission.hop_size != job.profile.hop_size))):
            raise PaneResourceError("owner admission differs from the scheduled capture")

    def _required_runtime(self, resource_id: str) -> _Runtime:
        try:
            return self._runtimes[resource_id]
        except KeyError:
            raise PaneResourceError("unknown physical receiver resource") from None


__all__ = [
    "PaneActivation", "PaneCaptureAdmission", "PaneCaptureOwner", "PaneDelivery", "PaneResourceError",
    "PaneHostTiming", "PaneResourcePreview", "PaneResourcePlanPreview", "PaneResourceSession",
]
