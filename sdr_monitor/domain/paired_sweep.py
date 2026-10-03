"""Scalar paired Sweep admission protocol; no I/O, IQ, arrays or GUI ownership.

Native coordination/assembly remains C++. These values freeze the metadata and
stale-result fence, not an enabled backend or evidence of dual physical RF.
"""

from dataclasses import dataclass
import math

from .continuous_sweep_geometry import SweepStepGeometry, sweep_segment_count, sweep_step_geometry
from .continuous_sweep_request import ContinuousSweepPlanRequest
from .identity import TimestampQuality
from .live import BackendKind, LiveConfiguration, LiveSnapshot, LiveSessionState
from .paired_live import PairedLiveRequest, validate_paired_selection_snapshot
from .receiver_topology import ReceiverChainSelection
from .sweep_capacity import SWEEP_MAX_SEGMENTS


@dataclass(frozen=True, slots=True)
class PairedSweepRunIdentity:
    """Application-assigned attempt identity, not a native/RF clock epoch.

    Only an admitted owner's Start installs this as active. Failed attempts
    consume their number; constructing this value never authorizes hardware.
    The request retains exact device/serial/topology/session/plan/profile facts.
    """

    request: "PairedSweepRequest"
    acquisition_epoch: int
    start_snapshot: LiveSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.request, PairedSweepRequest):
            raise TypeError("paired Sweep run requires an exact typed request")
        _counter(self.acquisition_epoch, minimum=1)
        self.request.validate_applied(self.start_snapshot, self.request.selection_revision)


def _text(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("paired Sweep requires exact nonblank identities")


def _counter(value: int, *, minimum: int = 0, maximum: int = (1 << 64) - 1) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("paired Sweep counter is outside its observed integer range")


@dataclass(frozen=True, slots=True)
class PairedSweepRequest:
    """One explicitly proposed LO plan; individual pane crops are downstream.

    `sweep.epoch` remains a lower bound, NOT an observed acquisition epoch.
    Full native geometry/combined memory budget/readback still gate Start.
    """

    resource_id: str
    pair: PairedLiveRequest
    sweep: ContinuousSweepPlanRequest
    selection_revision: int
    selected_snapshot: LiveSnapshot

    def __post_init__(self) -> None:
        _text(self.resource_id)
        if not isinstance(self.pair, PairedLiveRequest) or not isinstance(self.sweep, ContinuousSweepPlanRequest):
            raise TypeError("paired Sweep requires typed pair and common Sweep requests")
        _counter(self.selection_revision)
        if not isinstance(self.selected_snapshot, LiveSnapshot):
            raise TypeError("paired Sweep requires current observed selection facts")
        if self.pair.configuration.backend is not BackendKind.CPU:
            raise ValueError("paired Sweep requires an explicit CPU profile; no implicit fallback")
        sweep_segment_count(self.sweep)
        self.validate_selected(self.selected_snapshot, self.selection_revision)

    def validate_selected(self, snapshot: LiveSnapshot, revision: int) -> None:
        if not isinstance(snapshot, LiveSnapshot):
            raise TypeError("paired Sweep requires a current typed snapshot")
        validate_paired_selection_snapshot(snapshot, self.pair.device_id, self.pair.session_id, self.pair.topology)
        device, original = snapshot.device, self.selected_snapshot.device
        if (type(revision) is not int or revision != self.selection_revision
                or device is None or original is None
                or (device.identity_key, device.serial, device.capability_snapshot)
                != (original.identity_key, original.serial, original.capability_snapshot)
                or snapshot.state not in (LiveSessionState.CONNECTED, LiveSessionState.RUNNING)
                or snapshot.error is not None
                or snapshot.stop_required and snapshot.state is not LiveSessionState.RUNNING):
            raise ValueError("paired Sweep selected identity/session/revision is no longer current")

    def validate_applied(self, snapshot: LiveSnapshot, revision: int) -> None:
        """Prepared stopped profile fence, NOT proof of native RF readback."""
        self.validate_selected(snapshot, revision)
        if (snapshot.state is not LiveSessionState.CONNECTED or snapshot.stop_required
                or snapshot.error is not None):
            raise ValueError("paired Sweep requires a stopped current owner; no hidden restart")
        self.pair.validate_snapshot(snapshot)


@dataclass(frozen=True, slots=True)
class PairedSweepStepIdentity:
    """Observed active step, installed by the SAME control/acquisition owner.

    Invalidate before Stop/plan change; new step uses actual generation/epochs.
    Python callers cannot authorize RF merely by constructing this receipt.
    """

    resource_id: str
    session_id: str
    sweep_epoch: int
    line_sequence: int
    segment_index: int
    config_generation: int
    acquisition_epoch: int
    synchronization_epoch: int
    plan: ContinuousSweepPlanRequest
    profile: LiveConfiguration
    selection_revision: int
    usable_start_hz: float
    usable_stop_hz: float
    center_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    fft_size: int

    def __post_init__(self) -> None:
        _text(self.resource_id)
        _text(self.session_id)
        _counter(self.sweep_epoch)
        _counter(self.line_sequence)
        _counter(self.segment_index, maximum=SWEEP_MAX_SEGMENTS - 1)
        if (not isinstance(self.plan, ContinuousSweepPlanRequest)
                or not isinstance(self.profile, LiveConfiguration)):
            raise TypeError("paired Sweep active receipt requires exact immutable admitted intent")
        _counter(self.selection_revision)
        SweepStepGeometry(self.segment_index, self.usable_start_hz, self.usable_stop_hz)
        for value in (self.config_generation, self.acquisition_epoch, self.synchronization_epoch):
            _counter(value, minimum=1)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in (self.center_hz, self.sample_rate_hz, self.analog_bandwidth_hz)):
            raise ValueError("paired Sweep step requires actual finite positive center, Fs and RF bandwidth")
        _counter(self.fft_size, minimum=256, maximum=262_144)
        if self.fft_size & (self.fft_size - 1):
            raise ValueError("paired Sweep step requires actual power-of-two FFT")


@dataclass(frozen=True, slots=True)
class PairedSweepStepObservation:
    """Actual one-chain segment provenance, not a full-line RF timestamp."""

    identity: PairedSweepStepIdentity
    receiver_selection: ReceiverChainSelection
    producer_source_id: str
    center_hz: float
    sample_rate_hz: float
    fft_size: int
    frame_sequence: int
    first_sample_index: int
    timestamp_ns: int
    timestamp_quality: TimestampQuality
    clock_domain: str | None
    quality_flags: int
    shared_input_gaps_before: int

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PairedSweepStepIdentity):
            raise TypeError("paired Sweep requires an observed typed step identity")
        if (not isinstance(self.receiver_selection, ReceiverChainSelection)
                or self.receiver_selection not in (ReceiverChainSelection.RX1, ReceiverChainSelection.RX2)):
            raise ValueError("paired Sweep observation requires one typed RX chain")
        _text(self.producer_source_id)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in (self.center_hz, self.sample_rate_hz)):
            raise ValueError("paired Sweep requires actual finite positive center and Fs")
        _counter(self.fft_size, minimum=256, maximum=262_144)
        if self.fft_size & (self.fft_size - 1):
            raise ValueError("paired Sweep requires actual power-of-two FFT")
        if ((self.center_hz, self.sample_rate_hz, self.fft_size)
                != (self.identity.center_hz, self.identity.sample_rate_hz, self.identity.fft_size)):
            raise ValueError("paired Sweep observed geometry differs from active applied readback")
        for value in (self.frame_sequence, self.first_sample_index, self.timestamp_ns, self.shared_input_gaps_before):
            _counter(value)
        _counter(self.quality_flags, maximum=(1 << 32) - 1)
        if not isinstance(self.timestamp_quality, TimestampQuality):
            raise TypeError("paired Sweep timestamp provenance must remain typed")
        if self.clock_domain is not None:
            _text(self.clock_domain)


@dataclass(frozen=True, slots=True)
class PairedSweepStepPair:
    """Aligned observations of ONE shared native step, with distinct RX masks."""

    primary: PairedSweepStepObservation
    secondary: PairedSweepStepObservation

    def __post_init__(self) -> None:
        a, b = self.primary, self.secondary
        if not isinstance(a, PairedSweepStepObservation) or not isinstance(b, PairedSweepStepObservation):
            raise TypeError("paired Sweep needs both typed observations")
        if (a.receiver_selection is not ReceiverChainSelection.RX1
                or b.receiver_selection is not ReceiverChainSelection.RX2
                or a.producer_source_id == b.producer_source_id
                or (a.identity, a.center_hz, a.sample_rate_hz, a.fft_size, a.frame_sequence,
                    a.first_sample_index, a.timestamp_ns, a.timestamp_quality, a.clock_domain,
                    a.shared_input_gaps_before)
                != (b.identity, b.center_hz, b.sample_rate_hz, b.fft_size, b.frame_sequence,
                    b.first_sample_index, b.timestamp_ns, b.timestamp_quality, b.clock_domain,
                    b.shared_input_gaps_before)):
            raise ValueError("paired Sweep requires both chains of the same actual synchronized step")

    def validate_active(self, request: PairedSweepRequest, active: PairedSweepStepIdentity | None) -> None:
        """Refuse stopped/stale/foreign output BEFORE reduced data is admitted."""
        if not isinstance(request, PairedSweepRequest):
            raise TypeError("paired Sweep admission requires a typed current request")
        a, b = self.primary, self.secondary
        if (not isinstance(active, PairedSweepStepIdentity) or a.identity != active
                or active.resource_id != request.resource_id or active.session_id != request.pair.session_id
                or active.sweep_epoch < request.sweep.epoch
                or active.plan != request.sweep or active.profile != request.pair.configuration
                or active.selection_revision != request.selection_revision
                or active.segment_index >= sweep_segment_count(request.sweep)
                or a.producer_source_id != request.pair.primary_source_id
                or b.producer_source_id != request.pair.secondary_source_id
                or a.sample_rate_hz != request.pair.configuration.sample_rate_hz
                or a.fft_size != request.pair.configuration.fft_size):
            raise ValueError("paired Sweep result is stopped, stale or outside the current exact group")
        geometry = sweep_step_geometry(request.sweep, active.segment_index)
        if ((active.usable_start_hz, active.usable_stop_hz)
                != (geometry.usable_start_hz, geometry.usable_stop_hz)):
            raise ValueError("paired Sweep declared step differs from the current exact common LO plan")
        half_width = min(active.sample_rate_hz, active.analog_bandwidth_hz) / 2.
        if (active.center_hz - half_width > geometry.usable_start_hz
                or active.center_hz + half_width < geometry.usable_stop_hz):
            raise ValueError("paired Sweep actual readback does not cover the declared usable step")
