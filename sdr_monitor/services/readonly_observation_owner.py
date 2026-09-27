"""Low-rate injected read-only port ownership; no capability or radio model."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

_FAILURE = "Read-only observation/release was not confirmed; explicit close may be required"


class ReadOnlyProbePort[Probe](Protocol):
    def probe(self) -> Probe: ...
    def close(self) -> None: ...


class ReadOnlyObservationError(RuntimeError):
    """Redacted boundary failure, including an outstanding cleanup obligation."""


class RetainedReadOnlyObserver[Probe]:
    """Factories must return their owner before resource acquisition in probe.

    This serializes one instance, not unrelated providers or a global SDK.
    A common source router still supplies the cross-family ownership gate.
    """

    def __init__(self, factory: Callable[[], ReadOnlyProbePort[Probe]]) -> None:
        self._factory = factory
        self._pending: ReadOnlyProbePort[Probe] | None = None
        self._lock = threading.RLock()

    @property
    def cleanup_pending(self) -> bool:
        with self._lock:
            return self._pending is not None

    def close(self) -> None:
        with self._lock:
            if self._pending is None:
                return
            try:
                self._pending.close()
            except Exception:  # noqa: BLE001 - SDK cleanup must retain ownership and redact details.
                raise ReadOnlyObservationError(_FAILURE) from None
            self._pending = None

    def observe(self) -> Probe:
        with self._lock:
            if self._pending is not None:
                raise ReadOnlyObservationError(_FAILURE)
            try:
                owner = self._factory()
                self._pending = owner
                result = owner.probe()
            except Exception:  # noqa: BLE001 - injected port failures must not leak device details.
                raise ReadOnlyObservationError(_FAILURE) from None
            finally:
                # A close failure supersedes the read failure and preserves owner.
                # New observe never performs a hidden close/retry.
                self.close()
            return result
