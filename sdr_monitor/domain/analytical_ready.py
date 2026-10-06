"""Ephemeral detector-ready evidence, distinct from RF time and paint time.

Clock bounds use ordering of measured native/host brackets, NOT an assumed
shared clock epoch, fixed offset, oscillator rate or extrapolation. These
scalar receipts do not represent a complete offer/disposition ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .identity import ConfigurationGeneration, SessionId, SourceId
from .host_clock import HostClockScope


def _integer(value: object, label: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside its integer contract")


@dataclass(frozen=True, slots=True)
class ReadyClockBracket:
    native_ns: int
    host_before_ns: int
    host_after_ns: int

    def __post_init__(self) -> None:
        for label in ("native_ns", "host_before_ns", "host_after_ns"):
            _integer(getattr(self, label), label, -(1 << 63), (1 << 63) - 1)
        if self.host_after_ns < self.host_before_ns:
            raise ValueError("host clock regressed within native probe")


@dataclass(frozen=True, slots=True)
class ReadyHostBounds:
    """Conservative host interval for a strictly enclosed native ready time."""

    lower: ReadyClockBracket
    upper: ReadyClockBracket
    host_clock: HostClockScope | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.lower, ReadyClockBracket) or not isinstance(self.upper, ReadyClockBracket):
            raise ValueError("ready bounds require immutable clock brackets")
        if (self.lower.native_ns >= self.upper.native_ns
                or self.lower.host_after_ns > self.upper.host_before_ns):
            raise ValueError("ready probes are not monotonically ordered")
        if self.host_clock is not None and not isinstance(self.host_clock, HostClockScope):
            raise ValueError("ready bounds host clock must be typed or unknown")

    @property
    def earliest_host_ns(self) -> int:
        return self.lower.host_before_ns

    @property
    def latest_host_ns(self) -> int:
        return self.upper.host_after_ns

    @property
    def uncertainty_ns(self) -> int:
        return self.latest_host_ns - self.earliest_host_ns


class ReadyClockMapping(StrEnum):
    BOUNDED = "bounded_by_measured_probes"
    OUTSIDE_SAMPLES = "outside_measured_probes"
    PRODUCER_REGRESSED = "native_producer_clock_regressed"
    PROBE_REGRESSED = "measured_clock_regressed"
    PROBE_FAILED = "native_clock_probe_failed"


@dataclass(frozen=True, slots=True)
class DetectorReadyReceipt:
    # Host adapter-instance scope binds one retained native module object.
    # It is not an attested DLL hash or a globally shared producer identity.
    adapter_clock_scope_id: str
    host_process_id: int
    producer_instance_id: int
    offer_sequence: int
    config_generation: ConfigurationGeneration
    ready_native_ns: int
    source_id: SourceId
    receiver_id: str | None
    acquisition_epoch: int | None
    session_id: SessionId | None
    mapping: ReadyClockMapping
    host_bounds: ReadyHostBounds | None = None
    # Only assigned after SAME-owner native journal producer validation.
    # Legacy/vendor/replay receipts have no authenticated owner-run binding.
    owner_run_id: str | None = None

    def __post_init__(self) -> None:
        if (type(self.adapter_clock_scope_id) is not str or not self.adapter_clock_scope_id.strip()
                or self.adapter_clock_scope_id != self.adapter_clock_scope_id.strip()
                or type(self.source_id) is not str or not self.source_id.strip()
                or self.source_id != self.source_id.strip()):
            raise ValueError("ready receipt requires exact scope and source")
        for label in ("host_process_id", "producer_instance_id", "offer_sequence"):
            _integer(getattr(self, label), label, 1, (1 << 64) - 1)
        _integer(self.config_generation, "config_generation", 0, (1 << 64) - 1)
        _integer(self.ready_native_ns, "ready_native_ns", -(1 << 63), (1 << 63) - 1)
        if self.acquisition_epoch is not None:
            _integer(self.acquisition_epoch, "acquisition_epoch", 1, (1 << 64) - 1)
        for label in ("receiver_id", "session_id", "owner_run_id"):
            value = getattr(self, label)
            if value is not None and (type(value) is not str or not value.strip() or value != value.strip()):
                raise ValueError(f"ready receipt has invalid {label}")
        if self.owner_run_id is not None and (len(self.owner_run_id) > 4096
                or "\x00" in self.owner_run_id or self.owner_run_id == "unknown"):
            raise ValueError("ready receipt owner run must be exact and bounded")
        if not isinstance(self.mapping, ReadyClockMapping):
            raise ValueError("ready mapping must be typed")
        if self.mapping is ReadyClockMapping.BOUNDED:
            if (not isinstance(self.host_bounds, ReadyHostBounds)
                    or not self.host_bounds.lower.native_ns < self.ready_native_ns < self.host_bounds.upper.native_ns):
                raise ValueError("ready time must be strictly inside measured native probes")
            if (self.host_bounds.host_clock is not None
                    and self.host_bounds.host_clock.process_id != self.host_process_id):
                raise ValueError("ready bounds belong to another host process")
        elif self.host_bounds is not None:
            raise ValueError("unknown ready mapping cannot claim host bounds")
