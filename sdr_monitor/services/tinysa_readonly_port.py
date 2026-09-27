"""One lazy, retained version-only owner; no measurement or settings command."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .tinysa_capability_adapter import TinySaReadOnlyProbe
from .tinysa_serial_source_backend import TinySaSerialSourceBackend
from .tinysa_serial_version_probe import TinySaVersionSerialPort, _make_serial, _parse_version, _read_bounded_response
from .tinysa_source_composition import TinySaIdentityAssurance, TinySaTransportEndpoint


class TinySaSerialReadOnlyPort:
    """Return this owner before acquisition so failed close remains reachable.

    A stable observed USB serial and a uniquely resolved endpoint are required
    for this canonical capability mapper. Location/COM routes stay unverified.
    Neither version nor USB serial proves RF/metrological/device continuity.
    """

    def __init__(self, backend: TinySaSerialSourceBackend, endpoint: TinySaTransportEndpoint, *,
                 serial_factory: Callable[[str], TinySaVersionSerialPort] = _make_serial) -> None:
        self._backend = backend
        self._endpoint = endpoint
        self._serial_factory = serial_factory
        self._port: TinySaVersionSerialPort | None = None
        self._used = False
        self._closing = False
        self._lock = threading.RLock()

    def probe(self) -> TinySaReadOnlyProbe:
        with self._lock:
            if self._used or self._closing:
                raise RuntimeError("tinySA read-only owner requires explicit release")
            self._used = True
            expected = self._endpoint
            if expected.assurance is not TinySaIdentityAssurance.USB_SERIAL:
                raise RuntimeError("tinySA canonical identity is unverified")
            current = self._backend.resolve_endpoint(expected)
            port = self._serial_factory(current.route)
            self._port = port  # before ANY open, including partial-open failure
            port.dtr = False
            port.rts = False
            port.open()
            _read_bounded_response(port, deadline_seconds=0.25, limit=4096)
            command = b"version\r"
            if port.write(command) != len(command):
                raise RuntimeError("tinySA version command was not completely written")
            port.flush()
            response = _read_bounded_response(port, deadline_seconds=2.0, limit=4096)
            if b"ch> " not in response:
                raise RuntimeError("tinySA version response has no terminal prompt")
            observed = _parse_version(response)
            after = self._backend.resolve_endpoint(expected)
            if after != current:
                raise RuntimeError("tinySA endpoint changed during read-only observation")
            return TinySaReadOnlyProbe(observed.model, expected.identity_key, observed.normalized_version)

    def close(self) -> None:
        with self._lock:
            self._closing = True
            if self._port is None:
                return
            # Keep the Python serial object on every failure. Its public close
            # is idempotent; no raw OS handle is ever retried by this wrapper.
            self._port.close()
            if self._port.is_open:
                raise RuntimeError("tinySA serial release was not confirmed")
            self._port = None


__all__ = ["TinySaSerialReadOnlyPort"]
