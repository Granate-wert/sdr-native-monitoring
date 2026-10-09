"""Explicit diagnostic watchdog independent of Qt timers and processEvents.

CPython's faulthandler watchdog thread writes one traceback after a deadline,
including when Qt blocks the main thread. It does NOT stop/kill/restart an
application, release SDR leases, declare recovery, or diagnose an RF cause.
Use only in an explicitly selected diagnostic process owning faulthandler's
process-global dump_traceback_later facility. Foreign users of that facility
cannot be detected/restored by Python; do not install in normal product UI.
"""
from __future__ import annotations

import faulthandler
import json
from math import isfinite
from threading import Lock
from time import perf_counter_ns
from typing import IO

_OWNERSHIP_LOCK = Lock()
_owner: QtStallWatchdog | None = None


class QtStallWatchdog:
    """Inert constructor; explicit context arms one bounded, nonfatal dump."""

    def __init__(self, trace_file: IO[str], timeout_s: float) -> None:
        if (type(timeout_s) not in (int, float) or not isfinite(timeout_s)
                or not 0.05 <= timeout_s <= 3600):
            raise ValueError("diagnostic watchdog deadline must be finite, 0.05..3600 seconds")
        self._file = trace_file
        self._timeout = timeout_s
        self._armed = False
        self._closed = False
        self._phase_sequence = 0

    def arm(self) -> QtStallWatchdog:
        global _owner
        with _OWNERSHIP_LOCK:
            if self._closed or self._armed or _owner is not None:
                raise RuntimeError("diagnostic watchdog already owned, armed or closed")
            # A real writable file descriptor is required by faulthandler.
            # Exceptions leave our owner token unclaimed; no foreign cancel.
            faulthandler.dump_traceback_later(self._timeout, repeat=False, file=self._file, exit=False)
            _owner = self
            self._armed = True
        return self

    def rearm_phase(self, phase: str) -> None:
        """Explicit diagnostic phase marker and fresh one-shot, SAME owner.

        Whole-run one-shots can expire before a later risky phase. This API is
        opt-in for diagnostic harnesses only: it neither enforces a deadline
        nor supplies product state/cleanup authority. A previous dump remains
        in the file. Labels are bounded and no failed/foreign request cancels
        another owner's timer. At most256 markers (<256KiB) per instance.
        """
        global _owner
        if (type(phase) is not str or not phase or len(phase) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in phase)
                or len(phase.encode("utf-8")) > 128):
            raise ValueError("diagnostic phase must be nonempty, bounded128 UTF-8 bytes without controls")
        with _OWNERSHIP_LOCK:
            if self._closed or not self._armed or _owner is not self:
                raise RuntimeError("diagnostic phase requires the active SAME watchdog owner")
            if self._phase_sequence >= 256:
                raise RuntimeError("diagnostic phase marker bound exceeded")
            faulthandler.cancel_dump_traceback_later()
            self._armed = False
            try:
                marker = json.dumps({"event": "diagnostic_phase_arming", "phase": phase,
                    "sequence": self._phase_sequence + 1, "host_monotonic_ns": perf_counter_ns(),
                    "deadline_s": self._timeout, "deadline_enforced": False})
                self._file.write(marker + "\n")
                self._file.flush()
                faulthandler.dump_traceback_later(self._timeout, repeat=False,
                                                 file=self._file, exit=False)
            except BaseException:
                # Our prior timer was cancelled; do not retain a false armed
                # state or strand the process-global token after an IO error.
                _owner = None
                self._closed = True
                raise
            self._phase_sequence += 1
            self._armed = True

    def close(self) -> None:
        global _owner
        with _OWNERSHIP_LOCK:
            if self._armed and _owner is self:
                faulthandler.cancel_dump_traceback_later()
                _owner = None
            self._armed = False
            self._closed = True

    def __enter__(self) -> QtStallWatchdog:
        return self.arm()

    def __exit__(self, *_exc: object) -> None:
        self.close()
