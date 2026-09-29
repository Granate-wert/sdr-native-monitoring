"""Scalar identity/status of a display row, not analytical accumulation."""
from dataclasses import dataclass
from enum import StrEnum


class SweepRowState(StrEnum):
    PARTIAL = "partial"
    COMPLETE = "complete"
    GAP = "gap"


@dataclass(frozen=True, slots=True)
class SweepRowStamp:
    sequence: int
    revision: int
    state: SweepRowState
    acquisition_epoch: int | None = None

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (self.sequence, self.revision)):
            raise ValueError("Sweep display row identity must use nonnegative integers")
        if self.acquisition_epoch is not None and (
                type(self.acquisition_epoch) is not int or self.acquisition_epoch < 0):
            raise ValueError("Sweep display epoch must be a nonnegative integer or unknown")
        object.__setattr__(self, "state", SweepRowState(self.state))
        if self.state is SweepRowState.PARTIAL and self.revision == 0:
            raise ValueError("partial Sweep row requires a positive producer revision")

    def supersedes(self, previous: "SweepRowStamp") -> bool:
        return (self.acquisition_epoch == previous.acquisition_epoch
                and self.sequence == previous.sequence and previous.state is SweepRowState.PARTIAL
                and (self.state is not SweepRowState.PARTIAL or self.revision > previous.revision))
