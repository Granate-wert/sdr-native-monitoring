"""Bounded metadata for a presentation-only omission, never an RF gap claim."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OmittedMeasurement:
    source_id: str | None
    sequence: int | None
    epoch: int | None
    unit: str
    state: str
    revision: int | None = None
    raw_timestamp_ns: int | None = None
    clock_domain: str | None = None
    receiver_id: str | None = None
    configuration_generation: int | None = None
    missing_segments: int = 0
    pending_segments: int = 0
    gap_reasons: tuple[str, ...] = ()
    gap_reason_count: int = 0

    def __post_init__(self) -> None:
        for identity in (self.source_id, self.clock_domain, self.receiver_id):
            if identity is not None and not isinstance(identity, str):
                raise TypeError("omitted identity must be scalar text")
        if not isinstance(self.unit, str) or not isinstance(self.state, str):
            raise TypeError("omitted state/unit must be scalar text")
        for value in (self.sequence, self.epoch, self.revision, self.raw_timestamp_ns,
                      self.configuration_generation):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("omitted provenance must be a non-negative scalar integer")
        for value in (self.missing_segments, self.pending_segments, self.gap_reason_count):
            if type(value) is not int or value < 0:
                raise ValueError("omitted counts must be non-negative scalar integers")
        if (not isinstance(self.gap_reasons, tuple) or len(self.gap_reasons) > 8
                or any(not isinstance(reason, str) for reason in self.gap_reasons)
                or self.gap_reason_count < len(self.gap_reasons)):
            raise ValueError("omitted gap reasons must be bounded scalar text")


@dataclass(frozen=True, slots=True)
class PresentationOmission:
    """No frames/arrays/exception traceback retained by the refusal record."""
    requested_array_bytes: int
    limit_bytes: int
    measurements: tuple[OmittedMeasurement, ...]
    reason: str = "presentation_memory_budget"

    def __post_init__(self) -> None:
        if (type(self.requested_array_bytes) is not int or type(self.limit_bytes) is not int
                or self.requested_array_bytes < 0 or self.limit_bytes < 1
                or not isinstance(self.measurements, tuple) or len(self.measurements) > 2
                or self.reason != "presentation_memory_budget"):
            raise ValueError("invalid bounded presentation omission")
        if any(not isinstance(item, OmittedMeasurement) or len(item.gap_reasons) > 8
               for item in self.measurements):
            raise ValueError("invalid omitted measurement metadata")
