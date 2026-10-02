"""One RTBW acquisition group, two actual reduced producers; no Qt or RX I/O."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .live import LiveConfiguration, LiveSnapshot, LiveSessionState
from .receiver_topology import ReceiverChainSelection, ReceiverTopologySnapshot


def _identity(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("paired Live requires exact nonblank identities")


def _counter(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("paired Live requires observed nonnegative integer counters")


def validate_paired_selection_snapshot(snapshot: LiveSnapshot, device_id: str,
                                       session_id: str, topology: ReceiverTopologySnapshot) -> None:
    """Selection-only Stage admission; configuration remains a separate guard."""
    device = snapshot.device
    if (device is None or device.device_id != device_id or str(snapshot.session_id) != session_id
            or device.capabilities.receiver_topology != topology
            or not isinstance(topology, ReceiverTopologySnapshot)
            or not topology.supports_selection(ReceiverChainSelection.BOTH)
            or not device.serial or not device.identity_key or device.capability_snapshot is None):
        raise ValueError("paired Live selection/topology or stable identity is no longer exact")


@dataclass(frozen=True, slots=True)
class PairedLiveRequest:
    """Explicit caller producer IDs, bound to the current observed selection.

    These IDs become native SourceDescriptors, not inferred hardware serials.
    Digital topology admission is NOT a claim about physical RF paths.
    One configuration deliberately means shared LO/Fs/filter/gain/DSP policy.
    """

    device_id: str
    session_id: str
    topology: ReceiverTopologySnapshot
    configuration: LiveConfiguration
    primary_source_id: str
    secondary_source_id: str

    def __post_init__(self) -> None:
        for value in (self.device_id, self.session_id, self.primary_source_id, self.secondary_source_id):
            _identity(value)
        if self.primary_source_id == self.secondary_source_id:
            raise ValueError("paired native producers must have distinct source IDs")
        if not isinstance(self.configuration, LiveConfiguration):
            raise TypeError("paired Live requires the existing immutable Live configuration")
        if (not isinstance(self.topology, ReceiverTopologySnapshot)
                or not self.topology.supports_selection(ReceiverChainSelection.BOTH)):
            raise ValueError("paired Live requires an observed compatible dual-RX layout")

    def validate_snapshot(self, snapshot: LiveSnapshot) -> None:
        validate_paired_selection_snapshot(snapshot, self.device_id, self.session_id, self.topology)
        if snapshot.applied is None or snapshot.applied.applied != self.configuration:
            raise ValueError("paired Live selection/topology/profile or stable identity is no longer exact")


@dataclass(frozen=True, slots=True)
class PairedReceiverPerformance:
    source_id: str
    receiver_id: str
    fft_frames_computed: int
    fft_frames_dropped: int
    persistence_updates: int
    persistence_snapshots_superseded: int

    def __post_init__(self) -> None:
        _identity(self.source_id)
        if self.receiver_id not in {"RX1", "RX2"}:
            raise ValueError("paired receiver metrics require an actual selected chain")
        for value in (self.fft_frames_computed, self.fft_frames_dropped,
                      self.persistence_updates, self.persistence_snapshots_superseded):
            _counter(value)


@dataclass(frozen=True, slots=True)
class PairedLivePerformance:
    """Common stream once, per-chain analytical counters, group wall time once.

    No Fs-derived throughput, per-chain wall attribution or GUI paint claims.
    """

    iq_samples_received: int
    iq_blocks_received: int
    acquisition_queue_samples_dropped: int
    acquisition_queue_blocks_dropped: int
    paired_snapshots_emitted: int
    paired_snapshots_superseded: int
    paired_snapshots_abandoned: int
    shared_input_gaps: int
    paired_processing_ms: float
    primary: PairedReceiverPerformance
    secondary: PairedReceiverPerformance

    def __post_init__(self) -> None:
        for value in (self.iq_samples_received, self.iq_blocks_received,
                      self.acquisition_queue_samples_dropped, self.acquisition_queue_blocks_dropped,
                      self.paired_snapshots_emitted, self.paired_snapshots_superseded,
                      self.paired_snapshots_abandoned, self.shared_input_gaps):
            _counter(value)
        if not isfinite(self.paired_processing_ms) or self.paired_processing_ms < 0:
            raise ValueError("paired group wall time must be observed and finite")
        if (not isinstance(self.primary, PairedReceiverPerformance)
                or not isinstance(self.secondary, PairedReceiverPerformance)
                or self.primary.receiver_id != "RX1" or self.secondary.receiver_id != "RX2"
                or self.primary.source_id == self.secondary.source_id):
            raise ValueError("paired metrics must identify both actual producers")


@dataclass(frozen=True, slots=True)
class PairedLivePublication:
    """Bounded immutable pair, never one SpectrumFrame with a BOTH identity."""

    primary: LiveSnapshot
    secondary: LiveSnapshot
    synchronization_epoch: int
    first_sample_index: int
    shared_input_gaps_before: int

    def __post_init__(self) -> None:
        for value in (self.synchronization_epoch, self.first_sample_index, self.shared_input_gaps_before):
            _counter(value)
        left, right = self.primary, self.secondary
        a, b = left.spectrum, right.spectrum
        if (left.state is not LiveSessionState.RUNNING or right.state is not LiveSessionState.RUNNING
                or a is None or b is None or left.receiver_id != "RX1" or right.receiver_id != "RX2"
                or a.receiver_id != left.receiver_id or b.receiver_id != right.receiver_id
                or a.source_id == b.source_id or left.active_source_id != a.source_id
                or right.active_source_id != b.source_id
                or (left.session_id, left.acquisition_epoch, left.clock_domain, left.device, left.applied)
                != (right.session_id, right.acquisition_epoch, right.clock_domain, right.device, right.applied)
                or (a.sequence, a.timestamp_ns, a.config_generation, a.fft_size, a.hop_size,
                    a.center_frequency_hz, a.sample_rate_hz, a.unit)
                != (b.sequence, b.timestamp_ns, b.config_generation, b.fft_size, b.hop_size,
                    b.center_frequency_hz, b.sample_rate_hz, b.unit)
                or a.config_generation != left.active_config_generation
                or b.config_generation != right.active_config_generation
                or left.acquisition_epoch is None or left.clock_domain is None
                or a.acquisition_epoch != left.acquisition_epoch or b.acquisition_epoch != right.acquisition_epoch
                or a.clock_domain != left.clock_domain or b.clock_domain != right.clock_domain
                or a.unit != left.unit or b.unit != right.unit
                or a.numerical_provenance is None or b.numerical_provenance is None):
            raise ValueError("paired publication lacks coherent actual source/chain/geometry/epoch provenance")


__all__ = ["PairedLiveRequest", "PairedLivePublication", "PairedLivePerformance", "PairedReceiverPerformance", "validate_paired_selection_snapshot"]
