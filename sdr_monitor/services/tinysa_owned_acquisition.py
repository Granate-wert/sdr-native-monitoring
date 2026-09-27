"""Retained serial owner for one-shot or explicit repeated tinySA acquisition.

Construction is inert. One opened Python serial object supplies the fresh
version, observed zero offset and complete scanraw response. A failed close
keeps that exact object; there is no reopen, replacement port or raw-handle
retry. This is acquisition ownership, NOT RF/input/settings qualification or
an independent capability catalog. A caller still owns Start/Stop admission.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Protocol, cast

from .tinysa_capability_adapter import (
    TinySaCapabilityAdapter,
    TinySaCapabilityObservation,
    TinySaReadOnlyProbe,
)
from .tinysa_serial_trace_collector import (
    MAX_TINYSA_SCANRAW_READ_BYTES,
    TINYSA_ZERO_OFFSET_QUERY,
    TinySaScanRawRequest,
    TinySaTraceCollection,
    TinySaTraceCollectionCancelled,
    TinySaTraceCollectionError,
    TinySaTracePass,
    TinySaTraceSerialPort,
    _is_serial_port,
    _make_serial,
    collect_tinysa_open_pass,
    collect_tinysa_scanraw_trace,
)
from .tinysa_serial_version_probe import _parse_version
from .tinysa_source_composition import TinySaIdentityAssurance, TinySaTransportEndpoint


class TinySaEndpointResolver(Protocol):
    def resolve_endpoint(self, expected: TinySaTransportEndpoint) -> TinySaTransportEndpoint: ...


class TinySaOwnedAcquisition:
    """One reserved session of finite passes with mandatory same-owner cleanup.

    ``collect`` and ``collect_repeated`` run off Qt on the acquisition worker.
    Cancellation is cooperative, checked around each bounded PySerial read.
    The production serial factory has 50-ms reads and a 1-s write timeout;
    injected factories must uphold that contract. Close must be called after
    the operation has returned, not concurrently to steal an in-flight port.
    """

    def __init__(self, backend: TinySaEndpointResolver, endpoint: TinySaTransportEndpoint,
                 expected: TinySaCapabilityObservation, *,
                 serial_factory: Callable[[str], TinySaTraceSerialPort] = _make_serial,
                 monotonic: Callable[[], float] = time.monotonic,
                 monotonic_ns: Callable[[], int] = time.monotonic_ns) -> None:
        if not isinstance(endpoint, TinySaTransportEndpoint) or not isinstance(expected, TinySaCapabilityObservation):
            raise TypeError("tinySA acquisition requires retained endpoint and capability facts")
        if endpoint.assurance is not TinySaIdentityAssurance.USB_SERIAL:
            raise ValueError("tinySA acquisition requires a unique USB serial identity")
        if not all(callable(value) for value in (serial_factory, monotonic, monotonic_ns)):
            raise TypeError("tinySA acquisition requires an inert serial factory")
        self._backend, self._endpoint, self._expected = backend, endpoint, expected
        self._factory = serial_factory
        self._monotonic, self._monotonic_ns = monotonic, monotonic_ns
        self._port: TinySaTraceSerialPort | None = None
        self._cancel = threading.Event()
        self._operations = threading.Lock()
        self._request: TinySaScanRawRequest | None = None
        self._used = self._ready = self._closing = self._closed = False
        self._commands = 0
        self._operation_thread: int | None = None
        self._current_endpoint: TinySaTransportEndpoint | None = None
        self._measurement_pending = False
        self._pass_started_ns: int | None = None

    @property
    def cleanup_pending(self) -> bool:
        # Reserve before effects. A factory failure/unused attempt also has
        # an explicit close path, so the catalog cannot substitute its owner.
        return not self._closed

    @property
    def is_open(self) -> bool:
        return self._port is not None

    @property
    def dtr(self) -> bool:
        return False

    @dtr.setter
    def dtr(self, value: bool) -> None:
        if value is not False:
            raise ValueError("tinySA acquisition never enables DTR")

    @property
    def rts(self) -> bool:
        return False

    @rts.setter
    def rts(self, value: bool) -> None:
        if value is not False:
            raise ValueError("tinySA acquisition never enables RTS")

    def cancel(self) -> None:
        """No transport command or close from the cancelling caller."""
        self._cancel.set()

    @property
    def measurement_pending(self) -> bool:
        """A consumed scan command without a complete admitted response."""
        return self._measurement_pending

    @property
    def current_pass_elapsed_s(self) -> float | None:
        start = self._pass_started_ns
        return None if start is None else max(0.0, (self._monotonic_ns() - start) / 1e9)

    def _cancelled(self) -> None:
        if self._cancel.is_set():
            raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")

    def _endpoint_matches(self) -> TinySaTransportEndpoint:
        current = self._backend.resolve_endpoint(self._endpoint)
        if (current.assurance is not TinySaIdentityAssurance.USB_SERIAL
                or current.identity_key != self._endpoint.identity_key
                or self._current_endpoint is not None and current != self._current_endpoint):
            raise TinySaTraceCollectionError("tinySA selected endpoint changed")
        return current

    def _admit(self, request: TinySaScanRawRequest) -> None:
        if not isinstance(request, TinySaScanRawRequest):
            raise TypeError("tinySA acquisition requires a validated immutable request")
        snapshot = self._expected.snapshot
        if request.model.value != snapshot.model_id or not any(
            item.minimum <= request.start_frequency_hz < request.stop_frequency_hz <= item.maximum
            for item in snapshot.tuning_ranges_hz
        ):
            raise ValueError("tinySA request does not match retained model/input-range facts")

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        """No partial USB chunk is a spectrum; return only a closed complete trace."""
        self._admit(request)  # Pure validation before reserving or constructing serial.
        if not self._operations.acquire(blocking=False):
            raise TinySaTraceCollectionError("tinySA acquisition operation is already pending")
        try:
            self._operation_thread = threading.get_ident()
            if self._used or self._closing or self._closed:
                raise TinySaTraceCollectionError("tinySA acquisition requires explicit close/new Start")
            self._request = request
            result = collect_tinysa_scanraw_trace(self._endpoint.route, request,
                serial_factory=lambda _: self, cancel_requested=self._cancel.is_set,
                monotonic_ns=self._monotonic_ns)
            self._measurement_pending = False
            self._endpoint_matches()  # Changed/removed endpoint cannot publish a result.
            return result
        except TinySaTraceCollectionCancelled:
            raise
        except Exception:  # noqa: BLE001 - redact injected resolver/factory failures too.
            raise TinySaTraceCollectionError("tinySA owned trace collection failed closed") from None
        finally:
            self._operation_thread = None
            self._operations.release()

    def collect_repeated(self, request: TinySaScanRawRequest, publish: Callable[[TinySaTracePass], None], *,
                         interval_s: float = 0.1) -> None:
        """Explicit host loop on ONE serial object; no firmware continuous option.

        Every pass consumes its prompt before the next fresh version/zero/scan.
        Failure or cancellation exits and closes once; a failed close retains
        the owner. Publication is reduced data, not an unbounded command queue.
        """
        self._admit(request)
        if (not callable(publish) or isinstance(interval_s, bool) or not isinstance(interval_s, (int, float))
                or not math.isfinite(interval_s) or not 0.05 <= interval_s <= 60):
            raise ValueError("tinySA repeated pass interval is invalid")
        if not self._operations.acquire(blocking=False):
            raise TinySaTraceCollectionError("tinySA acquisition operation is already pending")
        try:
            self._operation_thread = threading.get_ident()
            if self._used or self._closing or self._closed:
                raise TinySaTraceCollectionError("tinySA acquisition requires explicit close/new Start")
            self._request = request
            try:
                self.open()
                while True:
                    self._cancelled()
                    self._pass_started_ns = self._monotonic_ns()
                    result = collect_tinysa_open_pass(self, request, cancel_requested=self._cancel.is_set,
                                                     monotonic_ns=self._monotonic_ns)
                    self._measurement_pending = False
                    self._endpoint_matches()
                    if not result.prompt_confirmed:
                        raise TinySaTraceCollectionError("tinySA completed pass lacks its prompt")
                    publish(result)
                    if self._cancel.wait(interval_s):
                        self._cancelled()
                    # No discard/reset. Only a completed prompt permits the
                    # next transaction on the SAME object and pinned endpoint.
                    self._commands = 0
                    self._verify_version(self._active_port(), self._endpoint_matches())
            finally:
                self._close_owned()
        except TinySaTraceCollectionCancelled:
            raise
        except Exception:  # noqa: BLE001 - never expose route/vendor/callback details.
            raise TinySaTraceCollectionError("tinySA repeated acquisition failed closed") from None
        finally:
            self._operation_thread = None
            self._operations.release()

    def open(self) -> None:
        if self._used or self._closing or self._closed or self._request is None:
            raise TinySaTraceCollectionError("tinySA acquisition cannot reopen a used owner")
        self._used = True  # Before ANY factory or SDK effect.
        self._cancelled()
        current = self._endpoint_matches()
        self._current_endpoint = current
        port = self._factory(current.route)
        self._port = cast(TinySaTraceSerialPort, port)  # Retain before validation/partial open.
        if not _is_serial_port(port) or not all(hasattr(port, name) for name in ("is_open", "dtr", "rts")):
            raise TinySaTraceCollectionError("tinySA serial factory contract is invalid")
        if port.is_open is not False:
            raise TinySaTraceCollectionError("tinySA factory must return an unopened serial object")
        port.dtr = port.rts = False
        port.open()
        if port.is_open is not True:
            raise TinySaTraceCollectionError("tinySA serial open was not confirmed")
        self._cancelled()
        # Discard a stale startup prompt before the fresh version query.
        port.reset_input_buffer()
        self._verify_version(port, current)
        self._ready = True

    def _verify_version(self, port: TinySaTraceSerialPort, current: TinySaTransportEndpoint) -> None:
        self._cancelled()
        if port.write(b"version\r") != len(b"version\r"):
            raise TinySaTraceCollectionError("tinySA version write was incomplete")
        port.flush()
        version = _parse_version(self._read_version(port))
        fresh = TinySaCapabilityAdapter.map_probe(TinySaReadOnlyProbe(
            version.model, current.identity_key, version.normalized_version))
        if fresh != self._expected:
            raise TinySaTraceCollectionError("tinySA model/firmware identity changed before measurement")
        self._cancelled()
        self._endpoint_matches()  # Same route/USB serial before zero/scan writes.

    def _read_version(self, port: TinySaTraceSerialPort) -> bytes:
        deadline = self._monotonic() + 2.0
        response = bytearray()
        while self._monotonic() < deadline:
            self._cancelled()
            size = min(256, 4096 - len(response) + 1)
            chunk = port.read(size)
            if not isinstance(chunk, bytes) or len(chunk) > size:
                raise TinySaTraceCollectionError("tinySA version read contract is invalid")
            response.extend(chunk)
            if len(response) > 4096:
                raise TinySaTraceCollectionError("tinySA version response exceeded its fixed bound")
            if b"ch> " in response:
                end = response.find(b"ch> ") + 4
                if response.count(b"ch> ") != 1 or any(value not in b"\t\r\n " for value in response[end:]):
                    raise TinySaTraceCollectionError("tinySA version response framing is invalid")
                return bytes(response)
        raise TinySaTraceCollectionError("tinySA version response deadline expired")

    def _active_port(self) -> TinySaTraceSerialPort:
        self._cancelled()
        if not self._ready or self._closing or self._closed or self._port is None:
            raise TinySaTraceCollectionError("tinySA acquisition owner is not ready")
        return self._port

    def reset_input_buffer(self) -> None:
        if self._commands:
            raise TinySaTraceCollectionError("tinySA acquisition cannot discard an active response")
        self._endpoint_matches()
        self._active_port().reset_input_buffer()

    def write(self, data: bytes) -> int:
        request = self._request
        if request is None or self._commands >= 2:
            raise TinySaTraceCollectionError("tinySA acquisition command count exceeded its bound")
        expected = TINYSA_ZERO_OFFSET_QUERY if self._commands == 0 else request.command
        if data != expected:
            raise TinySaTraceCollectionError("tinySA acquisition command is outside the exact request")
        self._endpoint_matches()
        port = self._active_port()
        if self._commands == 1:
            self._measurement_pending = True  # Partial writes are consumed too.
        self._commands += 1  # Even a partial write is consumed, never retried.
        return port.write(data)

    def flush(self) -> None:
        self._active_port().flush()

    def read(self, size: int = 1) -> bytes:
        if type(size) is not int or not 1 <= size <= MAX_TINYSA_SCANRAW_READ_BYTES:
            raise TinySaTraceCollectionError("tinySA acquisition read exceeds its bound")
        chunk = self._active_port().read(size)
        if not isinstance(chunk, bytes) or len(chunk) > size:
            raise TinySaTraceCollectionError("tinySA acquisition read contract is invalid")
        return chunk

    def close(self) -> None:
        """Confirm the same Python object's release; never reacquire a raw handle."""
        if self._operation_thread == threading.get_ident():
            self._close_owned()
            return
        if not self._operations.acquire(blocking=False):
            raise TinySaTraceCollectionError("Cancel and join the tinySA operation before close")
        try:
            self._close_owned()
        finally:
            self._operations.release()

    def _close_owned(self) -> None:
        if self._closed:
            return
        self._closing = True  # All commands permanently refused, including failed close.
        port = self._port
        if port is not None:
            try:
                port.close()  # Also on partial open/is_open=False.
                if getattr(port, "is_open", None) is not False:
                    raise TinySaTraceCollectionError("tinySA serial close was not confirmed")
            except Exception:  # noqa: BLE001 - retained/redacted transport boundary.
                raise TinySaTraceCollectionError("tinySA acquisition close failed; explicit close required") from None
            self._port = None
        self._ready = False
        self._closed = True


__all__ = ["TinySaOwnedAcquisition"]
