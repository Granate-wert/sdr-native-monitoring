"""Declared host measurement clock, never an RF clock or device identity.

Only the retained builtin perf_counter_ns callable is tagged automatically by
adapters. Injected/legacy clocks remain unknown. Equality permits comparison
within the same process; it does not authenticate a process lifetime or DLL.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HostClockKind(StrEnum):
    PERF_COUNTER_NS = "python_perf_counter_ns"


@dataclass(frozen=True, slots=True)
class HostClockScope:
    kind: HostClockKind
    process_id: int

    def __post_init__(self) -> None:
        if (not isinstance(self.kind, HostClockKind) or type(self.process_id) is not int
                or not 1 <= self.process_id < (1 << 64)):
            raise ValueError("host clock requires typed kind and exact process ID")
