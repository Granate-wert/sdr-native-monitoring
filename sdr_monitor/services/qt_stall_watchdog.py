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
from math import isfinite
from threading import Lock
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
