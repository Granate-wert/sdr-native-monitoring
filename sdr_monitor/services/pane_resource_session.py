"""Explicit APP-07 multi-resource control boundary, without Qt or SDR I/O.

The caller supplies one inert owner adapter per physical stream resource. This
session never discovers or opens a device in its constructor, and never infers
an RF path from digital I/Q scan elements. A future product adapter must prove
that its owner applies a CaptureJob and reports the resulting native identity.
"""

from __future__ import annotations

from _thread import LockType
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from math import isfinite
from threading import Lock, RLock
from time import monotonic
from typing import Callable, ContextManager, Iterator, Mapping, Protocol

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.pane_scheduler import (
    CaptureJob, CaptureMeasurementMode, PaneControlGap, PaneCrop, PaneRevisitEstimate, PaneSchedule,
    ResourcePaneSchedule,
)
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverEndpoint
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection

from .receiver_lease_manager import ReceiverLease, ReceiverLeaseManager


class PaneResourceError(RuntimeError):
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

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.capture_id, self.source_id, self.unit)):
            raise ValueError("capture admission identity, mode and unit are required")
        object.__setattr__(self, "mode", CaptureMeasurementMode(self.mode))
        endpoints = tuple(self.receiver_endpoint_ids)
        if not endpoints or len(set(endpoints)) != len(endpoints) or any(
                not isinstance(value, str) or not value for value in endpoints):
            raise ValueError("capture admission requires exact receiver endpoint identities")
        object.__setattr__(self, "receiver_endpoint_ids", endpoints)
        if type(self.acquisition_epoch) is not int or self.acquisition_epoch < 0:
            raise ValueError("capture admission requires a non-negative producer epoch")
        if self.config_generation is not None and (
                type(self.config_generation) is not int or self.config_generation < 0):
            raise ValueError("capture admission generation must be a non-negative integer")
        if (self.sample_rate_hz is None or not isfinite(self.sample_rate_hz)
                or self.sample_rate_hz <= 0
                or type(self.fft_size) is not int or self.fft_size < 2
                or type(self.hop_size) is not int or not 1 <= self.hop_size <= self.fft_size):
            raise ValueError("capture admission requires applied Fs/FFT/hop")
        if self.mode is CaptureMeasurementMode.RTBW and (self.session_id is None or not self.session_id):
            raise ValueError("RTBW admission requires a producer session")
        if self.mode is CaptureMeasurementMode.SWEEP and self.session_id is not None:
            raise ValueError("Sweep admission cannot claim an RTBW session")


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

    def validate_endpoint(self, endpoint: ReceiverEndpoint) -> None: ...
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

    @property
    def pane_id(self) -> str:
        return self.crop.pane_id


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
    admission: PaneCaptureAdmission | None = None
    current_activation: PaneActivation | None = None
    last_epoch: int | None = None
    last_session_id: str | None = None
    last_admission_epoch: int | None = None
    last_admission_session_id: str | None = None
    new_epoch_required: bool = False
    rejected_publications: int = 0
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
            if any(endpoint.selection is ReceiverChainSelection.BOTH for endpoint in group.endpoints):
                raise PaneResourceError("one spectrum bundle cannot represent both digital RX channels")
            group_sources = {endpoint.source_id for endpoint in group.endpoints}
            if len(group_sources) != 1 or source_ids.intersection(group_sources):
                raise PaneResourceError("each physical resource requires one distinct operational source")
            source_ids.update(group_sources)
            by_endpoint = {endpoint.endpoint_id: endpoint for endpoint in group.endpoints}
            planned = next(item for item in schedule.resources if item.physical_stream_resource_id == resource_id)
            if (len(by_endpoint) > 1 and any(job.profile.measurement_mode is CaptureMeasurementMode.SWEEP
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
            if (any(job.profile.measurement_mode is CaptureMeasurementMode.SWEEP for job in planned.jobs)
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
        self._applied = False

    @property
    def active_resource_count(self) -> int:
        with self._state_lock:
            return sum(runtime.active for runtime in self._runtimes.values())

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
                if self._applied or any(runtime.terminal for runtime in self._runtimes.values()):
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

    def start_resource(self, resource_id: str) -> PaneActivation:
        """One explicit Start; partial owner failure retains its lease for Stop."""
        runtime = self._required_runtime(resource_id)
        with runtime.control_lock:
            with self._owner_control_transaction(runtime):
                if self._recording_conflict(runtime):
                    raise PaneResourceError("recording conflicts with receiver Start")
                with self._state_lock:
                    if (not self._applied or runtime.lease is None or runtime.active
                            or runtime.stop_required or runtime.terminal or runtime.slot_index >= 0):
                        raise PaneResourceError("receiver is not in the applied, not-started state")
                    runtime.slot_index = 0
                    job = runtime.current_job
                    assert job is not None
                    runtime.activation_serial += 1
                    runtime.stop_required = True  # Reject frames until Start confirms.
                control_started = self._clock_sample_s()
                try:
                    admission = runtime.owner.start_capture(job)
                    self._validate_admission(runtime, job, admission)
                except Exception:
                    raise PaneResourceError("receiver Start did not confirm admission; explicit Stop is required") from None
                control_elapsed = self._elapsed_since(control_started)
                with self._state_lock:
                    activation = PaneActivation(resource_id, job.capture_id, 0, runtime.activation_serial,
                                                runtime.schedule.initial_control_gap, control_elapsed)
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
                except Exception:
                    raise PaneResourceError("receiver Stop did not confirm release; no retune was sent") from None
                with self._state_lock:
                    runtime.new_epoch_required = runtime.last_admission_epoch is not None
                    runtime.admission = None
                    runtime.slot_index = next_index
                    runtime.activation_serial += 1
                try:
                    admission = runtime.owner.start_capture(next_job)
                    self._validate_admission(runtime, next_job, admission)
                except Exception:
                    raise PaneResourceError("next receiver Start failed; owner retained for explicit Stop") from None
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
                    or runtime.current_activation is not activation
                    or job.capture_id != capture_id or admission.capture_id != capture_id
                    or endpoint is None or endpoint_id not in job.receiver_endpoint_ids
                    or endpoint.physical_stream_resource_id != resource_id
                    or not isinstance(bundle, AnalyzerFrameBundle) or identity is None
                    or identity.source_id != admission.source_id or bundle.mode != admission.mode
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
            if (len(grid) < 2 or any(crop.start_hz < float(grid[0]) or crop.stop_hz > float(grid[-1])
                                     for crop in job.crops if crop.receiver_endpoint_id == endpoint_id)):
                runtime.rejected_publications += 1
                return ()
            now = self._clock_sample_s()
            if now is None:
                runtime.rejected_publications += 1
                return ()
            runtime.last_epoch = admission.acquisition_epoch
            runtime.last_session_id = identity.session_id
            runtime.new_epoch_required = False
            deliveries = tuple(PaneDelivery(resource_id, capture_id, endpoint_id,
                                           runtime.activation_serial, crop, bundle, now)
                               for crop in job.crops if crop.receiver_endpoint_id == endpoint_id)
            for delivery in deliveries:
                self._pane_last_received[delivery.pane_id] = now
            return deliveries

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
            try:
                publications = runtime.owner.poll_bundles()
                if (not isinstance(publications, tuple) or len(publications) > 64
                        or any(not isinstance(item, tuple) or len(item) != 2
                               or not isinstance(item[0], str)
                               or not isinstance(item[1], AnalyzerFrameBundle)
                               for item in publications)):
                    raise ValueError("malformed bounded pane publication batch")
            except Exception:
                with self._state_lock:
                    runtime.active = False
                    runtime.current_activation = None
                    runtime.stop_required = True
                raise PaneResourceError("receiver publication failed; explicit Stop is required") from None
            delivered: list[PaneDelivery] = []
            for endpoint_id, bundle in publications:
                delivered.extend(self.accept_frame(activation, endpoint_id, bundle))
            return tuple(delivered)

    def pane_age_s(self, pane_id: str) -> float | None:
        with self._state_lock:
            resource_id = self._pane_resources.get(pane_id)
            if resource_id is None:
                raise PaneResourceError("unknown pane identity")
            if not self._runtimes[resource_id].active:
                return None
            received = self._pane_last_received.get(pane_id)
            now = self._clock_sample_s()
            return None if received is None or now is None or now < received else now - received

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
                with self._state_lock:
                    needs_stop = runtime.active or runtime.stop_required
                    runtime.active = False  # No late frame may reach a pane during join.
                    runtime.current_activation = None
                    runtime.stop_required = needs_stop
                if needs_stop:
                    try:
                        runtime.owner.stop_capture_and_wait()
                    except Exception:
                        raise PaneResourceError("receiver Stop did not confirm release; owner and lease retained") from None
                try:
                    runtime.owner.release_control_claim()
                except Exception:
                    with self._state_lock:
                        runtime.stop_required = False  # Hardware Stop succeeded; keep only the claim/lease obligation.
                    raise PaneResourceError("receiver control claim did not release; owner and lease retained") from None
                with self._state_lock:
                    assert runtime.lease is not None
                    runtime.lease.release()
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
        except Exception:
            with self._state_lock:
                if runtime.lease is not None and runtime.slot_index >= 0:
                    runtime.active = False
                    runtime.current_activation = None
                    runtime.stop_required = True
            raise PaneResourceError("owner control transaction failed; explicit Stop may be required") from None

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
                or (runtime.new_epoch_required and admission.session_id == runtime.last_admission_session_id
                    and runtime.last_admission_epoch is not None
                    and admission.acquisition_epoch <= runtime.last_admission_epoch)
                or admission.sample_rate_hz != job.profile.sample_rate_hz
                or admission.fft_size != job.profile.fft_size
                or admission.hop_size != job.profile.hop_size):
            raise PaneResourceError("owner admission differs from the scheduled capture")

    def _required_runtime(self, resource_id: str) -> _Runtime:
        try:
            return self._runtimes[resource_id]
        except KeyError:
            raise PaneResourceError("unknown physical receiver resource") from None


__all__ = [
    "PaneActivation", "PaneCaptureAdmission", "PaneCaptureOwner", "PaneDelivery", "PaneResourceError",
    "PaneResourcePreview", "PaneResourceSession",
]
