"""Bounded host-operation facts, never RF timing or an instrument watchdog.

One recorder per retained owner. No clock, serial, callback, response payload,
route, device handle or growing history is stored. Observation failure changes
only diagnostic completeness, never the measurement's control flow.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypedDict, cast

_MAX_COUNTER = (1 << 64) - 1
_MAX_NS = (1 << 63) - 1
_REASONS = frozenset({"unknown", "deadline", "framing", "bound", "identity", "transport", "close"})


class TinySaDiagnosticPhase(StrEnum):
    PREPARED = "prepared"
    OPEN = "open"
    ENDPOINT = "endpoint"
    VERSION = "version"
    ZERO = "zero"
    SCAN = "scan"
    SETTINGS = "settings"
    READBACK = "readback"
    PUBLISH = "publish"
    INTERVAL = "interval"
    CLOSE = "close"


class TinySaDiagnosticOperation(StrEnum):
    IDLE = "idle"
    FACTORY = "factory"
    OPEN = "open"
    RESET = "reset"
    WRITE = "write"
    FLUSH = "flush"
    READ = "read"
    VALIDATE = "validate"
    PARSE = "parse"
    PUBLISH = "publish"
    WAIT = "wait"
    CLOSE = "close"


class TinySaDiagnosticClock(StrEnum):
    MONOTONIC_SECONDS = "monotonic_seconds"
    MONOTONIC_NS = "monotonic_ns"


@dataclass(frozen=True, slots=True)
class TinySaProgressSnapshot:
    phase: TinySaDiagnosticPhase = TinySaDiagnosticPhase.PREPARED
    operation: TinySaDiagnosticOperation = TinySaDiagnosticOperation.IDLE
    in_flight: bool = False
    pass_id: int = 0
    command_id: int = 0
    command_accepted: bool | None = None
    read_entered: int = 0
    read_returned: int = 0
    response_bytes: int = 0
    prompt_confirmed: bool = False
    passes_completed: int = 0
    publications_completed: int = 0
    cancelled: bool = False
    deadline_clock: TinySaDiagnosticClock | None = None
    deadline_value: int | float | None = None
    observed_clock: TinySaDiagnosticClock | None = None
    observed_value: int | float | None = None
    diagnostic_failures: int = 0
    complete: bool = True


@dataclass(frozen=True, slots=True)
class TinySaFaultSnapshot:
    reason: str
    progress: TinySaProgressSnapshot


@dataclass(frozen=True, slots=True)
class TinySaAcquisitionDiagnosticSnapshot:
    progress: TinySaProgressSnapshot
    first_fault: TinySaFaultSnapshot | None


class _ProgressChanges(TypedDict, total=False):
    phase: TinySaDiagnosticPhase
    operation: TinySaDiagnosticOperation
    in_flight: bool
    pass_id: int
    command_id: int
    command_accepted: bool | None
    read_entered: int
    read_returned: int
    response_bytes: int
    prompt_confirmed: bool
    passes_completed: int
    publications_completed: int
    cancelled: bool
    deadline_clock: TinySaDiagnosticClock | None
    deadline_value: int | float | None
    observed_clock: TinySaDiagnosticClock | None
    observed_value: int | float | None


class TinySaDiagnosticRecorder:
    """Short independent scalar lock; callers never hold it across I/O.

    Absolute deadlines are supplied by the loop that owns them, in its actual
    clock domain. Observations reuse existing loop samples, not new clock reads.
    Counter overflow freezes that update and explicitly makes evidence partial.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = TinySaProgressSnapshot()
        self._first_fault: TinySaFaultSnapshot | None = None
        self._last_ns: int | None = None
        self._last_seconds: float | None = None

    def snapshot(self) -> TinySaAcquisitionDiagnosticSnapshot:
        with self._lock:
            return TinySaAcquisitionDiagnosticSnapshot(self._state, self._first_fault)

    def _partial(self) -> None:
        self._state = replace(self._state, complete=False,
            diagnostic_failures=min(_MAX_COUNTER, self._state.diagnostic_failures + 1))

    def _change(self, *, increments: tuple[str, ...] = (), **fields: object) -> None:
        with self._lock:
            try:
                for name in increments:
                    fields[name] = getattr(self._state, name) + 1
                for name in ("pass_id", "command_id", "read_entered", "read_returned", "response_bytes",
                             "passes_completed", "publications_completed"):
                    value = fields.get(name, getattr(self._state, name))
                    if type(value) is not int or not 0 <= value <= _MAX_COUNTER:
                        raise ValueError("diagnostic counter is not bounded")
                # Closed internal builders below supply the typed scalar keys;
                # counters additionally cross an explicit checked bound here.
                self._state = replace(self._state, **cast(_ProgressChanges, fields))
            except Exception:  # noqa: BLE001 - diagnostic failure cannot alter transport.
                self._partial()

    def begin_phase(self, phase: TinySaDiagnosticPhase, *, command: bool = False) -> None:
        fields: dict[str, object] = {"phase": phase, "operation": TinySaDiagnosticOperation.IDLE,
                                    "in_flight": False, "deadline_clock": None, "deadline_value": None,
                                    "observed_clock": None, "observed_value": None}
        if command:
            fields.update(command_accepted=None, read_entered=0, read_returned=0, response_bytes=0,
                          prompt_confirmed=False, deadline_clock=None, deadline_value=None,
                          observed_clock=None, observed_value=None)
        self._change(increments=("command_id",) if command else (), **fields)

    def begin_pass(self) -> None:
        self._change(increments=("pass_id",))

    def enter(self, operation: TinySaDiagnosticOperation) -> None:
        self._change(increments=("read_entered",) if operation is TinySaDiagnosticOperation.READ else (),
                     operation=operation, in_flight=True)

    def returned(self, operation: TinySaDiagnosticOperation, *, response_bytes: int = 0,
                 command_accepted: bool | None = None) -> None:
        # Byte counts are per current command, NOT transport throughput or RF samples.
        with self._lock:
            byte_total = self._state.response_bytes + response_bytes
        fields: dict[str, object] = {"operation": operation, "in_flight": False,
                                    "response_bytes": byte_total}
        if command_accepted is not None:
            fields["command_accepted"] = command_accepted
        self._change(increments=("read_returned",) if operation is TinySaDiagnosticOperation.READ else (),
                     **fields)

    def prompt(self) -> None:
        self._change(prompt_confirmed=True)

    def pass_completed(self) -> None:
        self._change(increments=("passes_completed",))

    def published(self) -> None:
        self._change(increments=("publications_completed",))

    def cancel(self) -> None:
        self._change(cancelled=True)

    def clock(self, clock: TinySaDiagnosticClock, observed: int | float, *,
              deadline: int | float | None = None) -> None:
        with self._lock:
            try:
                previous: int | float | None
                if clock is TinySaDiagnosticClock.MONOTONIC_NS:
                    if type(observed) is not int or not 0 <= observed <= _MAX_NS:
                        raise ValueError("invalid ns clock")
                    previous = self._last_ns
                    if deadline is not None and (type(deadline) is not int or not observed <= deadline <= _MAX_NS):
                        raise ValueError("invalid ns deadline")
                elif clock is TinySaDiagnosticClock.MONOTONIC_SECONDS:
                    if type(observed) is not float or not math.isfinite(observed) or observed < 0:
                        raise ValueError("invalid seconds clock")
                    previous = self._last_seconds
                    if deadline is not None and (type(deadline) is not float or not math.isfinite(deadline)
                                                 or deadline < observed):
                        raise ValueError("invalid seconds deadline")
                else:
                    raise ValueError("unknown clock domain")
                if previous is not None and observed < previous:
                    raise ValueError("diagnostic clock regressed")
                if clock is TinySaDiagnosticClock.MONOTONIC_NS:
                    self._last_ns = cast(int, observed)  # exact type checked above
                else:
                    self._last_seconds = cast(float, observed)
                fields: dict[str, object] = {"observed_clock": clock, "observed_value": observed}
                if deadline is not None:
                    fields.update(deadline_clock=clock, deadline_value=deadline)
                self._state = replace(self._state, **cast(_ProgressChanges, fields))
            except Exception:  # noqa: BLE001 - original loop still owns timing/admission.
                self._partial()
                self._state = replace(self._state, observed_clock=None, observed_value=None,
                                      deadline_clock=None, deadline_value=None)

    def fault(self, reason: str, *, cancelled: bool = False) -> None:
        with self._lock:
            if cancelled:
                self._state = replace(self._state, cancelled=True)
            elif self._first_fault is None:
                if reason not in _REASONS:
                    self._partial()
                    reason = "unknown"
                self._first_fault = TinySaFaultSnapshot(reason, self._state)
