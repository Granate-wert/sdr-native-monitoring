"""Bounded live IQ/Spectrum recording coordinator for S08."""

from __future__ import annotations

import base64
from importlib import import_module
import json
import os
import queue
import shutil
import struct
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from ..domain import (
    DEFAULT_RECORDING_RESOURCE_BUDGET,
    IQBlock,
    LossReason,
    RecordingHealth,
    RecordingOptions,
    RecordingResult,
    RecordingState,
    SpectrumFrame,
)


_SENTINEL = object()


def _array_payload(array: np.ndarray) -> dict[str, Any]:
    data = np.asarray(array)
    return {"dtype": data.dtype.str, "shape": list(data.shape), "data": base64.b64encode(data.tobytes()).decode("ascii")}


def _json_record(kind: str, value: Any) -> bytes:
    if isinstance(value, IQBlock):
        payload = {
            "kind": kind,
            "sequence": value.sequence,
            "timestamp_ns": value.timestamp_ns,
            "timestamp_quality": value.timestamp_quality.value,
            "loss_reasons": [reason.value for reason in value.loss_reasons],
            "sample_rate_hz": value.sample_rate_hz,
            "source_id": value.source_id,
            "config_generation": value.config_generation,
            "samples": _array_payload(np.asarray(value.samples)),
        }
    elif isinstance(value, SpectrumFrame):
        payload = {
            "kind": kind,
            "sequence": value.sequence,
            "timestamp_ns": value.timestamp_ns,
            "timestamp_quality": value.timestamp_quality.value,
            "loss_reasons": [reason.value for reason in value.loss_reasons],
            "unit": value.unit,
            "source_id": value.source_id,
            "config_generation": value.config_generation,
            "calibration_profile_id": value.calibration_profile_id,
            "frequencies_hz": _array_payload(value.frequencies_hz),
            "values": _array_payload(value.values),
        }
    else:
        payload = {"kind": kind, "value": value}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


class RecordingSourceBus:
    """Fan-out publication point. Subscribers must enqueue without blocking."""

    def __init__(self) -> None:
        self._sinks: set[Any] = set()
        self._lock = threading.RLock()

    def add_recording_sink(self, sink: Any) -> None:
        with self._lock:
            self._sinks.add(sink)

    def remove_recording_sink(self, sink: Any) -> None:
        with self._lock:
            self._sinks.discard(sink)

    def publish_iq(self, block: IQBlock) -> int:
        return self._publish("submit_iq", block)

    def publish_spectrum(self, frame: SpectrumFrame) -> int:
        return self._publish("submit_spectrum", frame)

    def publish_metadata(self, metadata: dict[str, Any]) -> int:
        return self._publish("submit_metadata", metadata)

    def _publish(self, method: str, value: Any) -> int:
        with self._lock:
            sinks = tuple(self._sinks)
        accepted = 0
        for sink in sinks:
            if getattr(sink, method)(value):
                accepted += 1
        return accepted


class RecordingService:
    """Historical bounded ``.sdrrec`` compatibility writer and reader.

    The current native application composition never selects this class for
    Pluto/AD936x Live recording.  Its JSON/base64 payload is retained solely
    for deterministic tests and backwards-compatible read/reprocess/recovery
    of historical files; native RTBW recording belongs to
    :class:`NativeLiveRecordingService` and keeps I/Q inside C++.
    """

    def __init__(self) -> None:
        self.source_bus = RecordingSourceBus()
        self._queue: queue.Queue[Any] | None = None
        self._worker: threading.Thread | None = None
        self._handle: BinaryIO | None = None
        self._options: RecordingOptions | None = None
        self._path: Path | None = None
        self._part: Path | None = None
        self._state = RecordingState.IDLE
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._iq_blocks = 0
        self._spectrum_frames = 0
        self._drops = 0
        self._gaps = 0
        self._bytes_written = 0
        self._error = ""
        self._metadata: dict[str, Any] = {}
        self._drop_reasons: set[LossReason] = set()

    def start(self, options: RecordingOptions) -> None:
        with self._lock:
            if self._state in (RecordingState.RECORDING, RecordingState.FINALIZING):
                raise RuntimeError("recording is already active")
            options = RecordingOptions(**{**options.__dict__}) if hasattr(options, "__dict__") else options
            path = Path(options.output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._options = options
            self._path = path
            self._part = path.with_suffix(path.suffix + ".part")
            self._handle = self._part.open("wb")
            self._queue = queue.Queue(maxsize=options.queue_capacity)
            self._stop.clear()
            self._state = RecordingState.RECORDING
            self._iq_blocks = self._spectrum_frames = self._drops = self._gaps = self._bytes_written = 0
            self._error = ""
            self._metadata = dict(options.metadata)
            self._drop_reasons.clear()
            header = {
                "schema": "sdr-native-recording",
                "version": 2,
                "record_iq": options.record_iq,
                "record_spectrum": options.record_spectrum,
                "sample_rate_hz": options.sample_rate_hz,
                "center_frequency_hz": options.center_frequency_hz,
                "resource_budget": {
                    "queue_capacity": options.queue_capacity,
                    "max_queue_bytes": DEFAULT_RECORDING_RESOURCE_BUDGET.estimate(options).max_queue_bytes,
                },
                "metadata": self._metadata,
            }
            self._write_bytes((json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            self._worker = threading.Thread(target=self._writer_loop, name="sdr-recording-writer", daemon=False)
            self._worker.start()
            self.source_bus.add_recording_sink(self)

    def start_now(self, options: RecordingOptions) -> None:
        """Reject native-only immediate-start semantics on the legacy writer."""

        del options
        raise RuntimeError(
            "Start recording now is available only for native RTBW Live; "
            "the legacy recorder cannot restart a live engine"
        )

    def submit_iq(self, block: IQBlock) -> bool:
        return self._submit("iq", block, enabled=lambda options: options.record_iq)

    def submit_spectrum(self, frame: SpectrumFrame) -> bool:
        return self._submit("spectrum", frame, enabled=lambda options: options.record_spectrum)

    def submit_metadata(self, metadata: dict[str, Any]) -> bool:
        return self._submit("metadata", dict(metadata), enabled=lambda _options: True)

    def _submit(self, kind: str, value: Any, *, enabled: Any) -> bool:
        with self._lock:
            options = self._options
            queue_ref = self._queue
            if (
                self._state is not RecordingState.RECORDING
                or queue_ref is None
                or options is None
                or not enabled(options)
            ):
                return False
            try:
                DEFAULT_RECORDING_RESOURCE_BUDGET.validate_submission(kind, value)
            except ValueError:
                self._note_drop(LossReason.RECORDER)
                return False
            try:
                queue_ref.put_nowait((kind, value))
            except queue.Full:
                self._note_drop(LossReason.RECORDER)
                return False
            return True

    def stop(self, timeout_s: float = 5.0) -> RecordingResult:
        """Stop without holding the service lock while the writer drains."""
        timeout = max(timeout_s, 0.1)
        deadline = time.monotonic() + timeout
        queue_ref: queue.Queue[Any] | None = None
        with self._lock:
            if self._state is RecordingState.IDLE:
                return self._result()
            if self._state is RecordingState.RECORDING and self._queue is not None:
                self._state = RecordingState.FINALIZING
                queue_ref = self._queue
                self.source_bus.remove_recording_sink(self)

        # The writer needs the same lock for counters.  Do not block while
        # holding it, otherwise a full queue deadlocks before the sentinel is
        # admitted (especially visible with Python 3.13 on Windows).
        if queue_ref is not None:
            remaining = max(deadline - time.monotonic(), 0.0)
            try:
                queue_ref.put(_SENTINEL, timeout=remaining)
            except queue.Full:
                # Preserve bounded shutdown: evict queued records explicitly
                # as gaps, then always enqueue the terminal marker.
                while True:
                    try:
                        queue_ref.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        with self._lock:
                            self._note_drop(LossReason.SHUTDOWN)
                    try:
                        queue_ref.put_nowait(_SENTINEL)
                        break
                    except queue.Full:
                        continue

        worker = self._worker
        if worker is not None:
            worker.join(timeout=max(deadline - time.monotonic(), 0.1))
        with self._lock:
            if worker is not None and worker.is_alive():
                self._state = RecordingState.STOP_TIMEOUT
                return self._result()
            if self._state is RecordingState.FINALIZING:
                self._state = RecordingState.COMPLETED
                if self._part is not None and self._path is not None:
                    os.replace(self._part, self._path)
            return self._result()

    def health(self) -> RecordingHealth:
        with self._lock:
            free = None
            if self._path is not None:
                try:
                    free = shutil.disk_usage(self._path.parent).free
                except OSError:
                    pass
            return RecordingHealth(
                self._state,
                self._queue.qsize() if self._queue is not None else 0,
                self._options.queue_capacity if self._options else 0,
                self._iq_blocks,
                self._spectrum_frames,
                self._drops,
                self._gaps,
                self._bytes_written,
                str(self._path) if self._path else None,
                self._error,
                free,
                tuple(sorted(self._drop_reasons, key=lambda reason: reason.value)),
            )

    def recover_partial(self, uri: Any) -> dict[str, Any]:
        path = Path(uri)
        native_scan = _scan_native_recording_prefix(path)
        if native_scan is not None:
            return native_scan
        if path.suffix != ".part" or not path.exists():
            return {"uri": str(path), "recovered": False, "reason": "partial file not found"}
        return {"uri": str(path), "recovered": True, "bytes": path.stat().st_size, "requires_finalize": True}

    def close(self) -> None:
        if self._state in (RecordingState.RECORDING, RecordingState.FINALIZING):
            self.stop()
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._state = RecordingState.IDLE if self._state is RecordingState.COMPLETED else self._state

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get() if self._queue is not None else _SENTINEL
            if item is _SENTINEL:
                break
            if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
                raise RuntimeError("recording queue item has an invalid shape")
            kind, value = item
            try:
                self._write_bytes(_json_record(kind, value))
                with self._lock:
                    if kind == "iq":
                        self._iq_blocks += 1
                    elif kind == "spectrum":
                        self._spectrum_frames += 1
            except (OSError, ValueError, TypeError) as error:
                with self._lock:
                    self._error = str(error)
                    self._state = RecordingState.FAILED
                break
        with self._lock:
            if self._handle is not None:
                self._handle.flush()
                self._handle.close()
                self._handle = None

    def _write_bytes(self, data: bytes) -> None:
        if self._handle is None:
            raise OSError("recording file is not open")
        self._handle.write(data)
        self._bytes_written += len(data)

    def _note_drop(self, reason: LossReason) -> None:
        self._drops += 1
        self._gaps += 1
        self._drop_reasons.add(reason)

    def _result(self) -> RecordingResult:
        return RecordingResult(
            str(self._path) if self._path else "",
            self._state,
            self._iq_blocks,
            self._spectrum_frames,
            self._drops,
            self._gaps,
            self._bytes_written,
            dict(self._metadata),
            self._error,
            tuple(sorted(self._drop_reasons, key=lambda reason: reason.value)),
        )


def _scan_native_recording_prefix(path: Path) -> dict[str, Any] | None:
    """Return read-only native recovery evidence when an artifact is present.

    Historical ``.sdrrec.part`` recovery remains untouched. Native artifacts
    are never repaired, truncated or finalized here: this control-plane method
    merely returns exact complete-prefix evidence from the portable native
    scanner.
    """
    try:
        native_module = import_module("sdr_monitor._sdr_native")
        scan_function = getattr(native_module, "scan_native_recording_prefix")
    except (ImportError, ModuleNotFoundError, OSError, AttributeError):
        return None
    try:
        scan = scan_function(str(path))
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "uri": str(path),
            "recovered": False,
            "scan_only": True,
            "reason": f"native recording scan failed: {error}",
        }
    iq_present = bool(
        scan.iq_manifest_final
        or scan.iq_manifest_partial
        or scan.iq_data_segments
        or scan.iq_index_complete_records
        or scan.iq_index_trailing_bytes
        or scan.iq_gap_complete_records
        or scan.iq_gap_trailing_bytes
    )
    spectrum_present = bool(
        scan.spectrum_manifest_final
        or scan.spectrum_manifest_partial
        or scan.spectrum_complete_records
        or scan.spectrum_complete_bytes
        or scan.spectrum_trailing_bytes
    )
    if not iq_present and not spectrum_present:
        return None
    incomplete = bool(scan.iq_manifest_partial or scan.spectrum_manifest_partial)
    return {
        "uri": str(path),
        "recovered": False,
        "scan_only": True,
        "requires_repair": incomplete,
        "iq": {
            "manifest_final": bool(scan.iq_manifest_final),
            "manifest_partial": bool(scan.iq_manifest_partial),
            "data_segments": int(scan.iq_data_segments),
            "data_bytes": int(scan.iq_data_bytes),
            "index_complete_records": int(scan.iq_index_complete_records),
            "index_complete_bytes": int(scan.iq_index_complete_bytes),
            "index_trailing_bytes": int(scan.iq_index_trailing_bytes),
            "gap_complete_records": int(scan.iq_gap_complete_records),
            "gap_complete_bytes": int(scan.iq_gap_complete_bytes),
            "gap_trailing_bytes": int(scan.iq_gap_trailing_bytes),
        },
        "spectrum": {
            "manifest_final": bool(scan.spectrum_manifest_final),
            "manifest_partial": bool(scan.spectrum_manifest_partial),
            "binary_header_valid": bool(scan.spectrum_binary_header_valid),
            "complete_records": int(scan.spectrum_complete_records),
            "complete_bytes": int(scan.spectrum_complete_bytes),
            "trailing_bytes": int(scan.spectrum_trailing_bytes),
        },
    }


class InMemoryRecordingService(RecordingService):
    """Compatibility name; unlike a synthetic producer it still records only submitted source data."""


__all__ = ["InMemoryRecordingService", "RecordingService", "RecordingSourceBus"]
