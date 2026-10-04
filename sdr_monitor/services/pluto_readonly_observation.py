"""One low-rate Pluto owner transaction; failed release remains reachable."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from ..domain.pluto_connection import PlutoUsbConnectionExpectation
from .ad936x_identity_admission import create_identity_bound_owner, normalized_pluto_serial


class PlutoObservationError(RuntimeError):
    """Redacted failure; callers must explicitly close a pending owner."""


_FAILURE = "Pluto observation/release was not confirmed; explicit Stop/close may be required"


@dataclass(frozen=True, slots=True)
class PlutoReadOnlyObservation:
    """Copied native facts, not another capability truth model or physical proof."""

    probe: Any = field(repr=False)
    capabilities: Any = field(repr=False)
    topology: Any = field(default=None, repr=False)
    coherent_context: bool = False


class PlutoReadOnlyObserver:
    def __init__(self, native_module: Any, *, timeout_ms: int = 3000) -> None:
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("Pluto observation timeout must be a positive integer")
        self._native = native_module
        self._timeout_ms = timeout_ms
        self._pending: Any = None
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
                self._pending.disconnect()
            except Exception:  # noqa: BLE001 - SDK cleanup must fail closed and redact details.
                raise PlutoObservationError(_FAILURE) from None
            self._pending = None

    def observe(self, uri: str, *, expected_serial: str | None = None,
                expected_usb_connection: PlutoUsbConnectionExpectation | None = None) -> PlutoReadOnlyObservation:
        if (not isinstance(uri, str) or uri != uri.strip() or
                not uri.startswith(("usb:", "ip:")) or not uri.split(":", 1)[1]):
            raise PlutoObservationError(_FAILURE)
        with self._lock:
            if self._pending is not None:
                raise PlutoObservationError(_FAILURE)
            try:
                owner = create_identity_bound_owner(
                    self._native, "PlutoDevice", uri, self._timeout_ms,
                    expected_serial=expected_serial,
                    expected_usb_connection=expected_usb_connection,
                )
                self._pending = owner
                probe = owner.probe()
                capabilities = owner.capabilities()
                if probe is None or capabilities is None:
                    raise PlutoObservationError(_FAILURE)
                topology_method = getattr(owner, "receiver_topology", None)
                topology = topology_method() if callable(topology_method) else None
                protocol = getattr(self._native, "PLUTO_OBSERVATION_PROTOCOL_VERSION", None)
                coherent = type(protocol) is int and protocol == 1 and topology is not None
                if coherent:
                    if getattr(probe, "uri", uri) != uri:
                        raise PlutoObservationError(_FAILURE)
                    _require_same_identity(probe, capabilities)
                    if topology is not None:
                        if getattr(topology.context, "uri", uri) != uri:
                            raise PlutoObservationError(_FAILURE)
                        _require_same_identity(probe, topology.context)
                result = PlutoReadOnlyObservation(probe, capabilities, topology, coherent)
            except Exception:  # noqa: BLE001 - any failed native read invalidates the transaction.
                raise PlutoObservationError(_FAILURE) from None
            finally:
                # A close exception supersedes the read error and keeps _pending.
                # No facts are published before successful release.
                self.close()
            return result


def _require_same_identity(probe: Any, other: Any) -> None:
    for attribute in ("serial", "firmware"):
        if not hasattr(other, attribute):
            continue  # optional older/test struct; missing facts are not invented.
        left = getattr(probe, attribute, None)
        right = getattr(other, attribute, None)
        if attribute == "serial":
            if normalized_pluto_serial(left) != normalized_pluto_serial(right):
                raise PlutoObservationError(_FAILURE)
        elif left != right:
            raise PlutoObservationError(_FAILURE)
