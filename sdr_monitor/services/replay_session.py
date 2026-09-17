"""Indexed recording reader and asynchronous replay/reprocess service."""

from __future__ import annotations

import json
import struct
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..domain import (
    FrameBus,
    IQBlock,
    LossReason,
    RecordingIndex,
    ReplayClock,
    ReplayIndexEntry,
    ReplayKind,
    ReplayPosition,
    ReplayState,
    ReprocessResult,
    SpectrumFrame,
    TimestampQuality,
    as_configuration_generation,
    as_frame_sequence,
    as_source_id,
    as_timestamp_ns,
)
from .native_replay import NativeIqReprocessor, NativeSpectrumRecordingReader, is_native_recording_uri


class RecordingReader:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle = self.path.open("rb")
        header = self._handle.readline()
        payload = json.loads(header.decode("utf-8"))
        if payload.get("schema") != "sdr-native-recording":
            raise ValueError("unsupported recording schema")
        entries: list[ReplayIndexEntry] = []
        while True:
            offset = self._handle.tell()
            length_raw = self._handle.read(4)
            if not length_raw:
                break
            if len(length_raw) != 4:
                raise ValueError("truncated recording frame length")
            length = struct.unpack("<I", length_raw)[0]
            raw = self._handle.read(length)
            if len(raw) != length:
                raise ValueError("truncated recording frame")
            item = json.loads(raw.decode("utf-8"))
            if item.get("kind") in (ReplayKind.IQ.value, ReplayKind.SPECTRUM.value):
                entries.append(
                    ReplayIndexEntry(
                        len(entries),
                        offset,
                        length + 4,
                        str(item["kind"]),
                        as_frame_sequence(item.get("sequence", 0)),
                        as_timestamp_ns(item.get("timestamp_ns", 0)),
                    )
                )
        self.index = RecordingIndex(str(self.path), tuple(entries), max((item.timestamp_ns for item in entries), default=0) - min((item.timestamp_ns for item in entries), default=0), self.path.stat().st_size)

    def read(self, entry: ReplayIndexEntry) -> IQBlock | SpectrumFrame:
        self._handle.seek(entry.offset)
        raw_length = self._handle.read(4)
        length = struct.unpack("<I", raw_length)[0]
        payload = json.loads(self._handle.read(length).decode("utf-8"))
        if payload["kind"] == ReplayKind.IQ.value:
            samples = _array_from_payload(payload["samples"])
            return IQBlock(
                as_frame_sequence(payload["sequence"]),
                as_timestamp_ns(payload["timestamp_ns"]),
                samples,
                float(payload["sample_rate_hz"]),
                as_source_id(payload.get("source_id", "replay")),
                as_configuration_generation(payload.get("config_generation", 0)),
                TimestampQuality(payload.get("timestamp_quality", TimestampQuality.UNKNOWN)),
                tuple(LossReason(value) for value in payload.get("loss_reasons", ())),
            )
        return SpectrumFrame(
            as_frame_sequence(payload["sequence"]),
            as_timestamp_ns(payload["timestamp_ns"]),
            _array_from_payload(payload["frequencies_hz"]),
            _array_from_payload(payload["values"]),
            str(payload.get("unit", "dBFS/bin")),
            as_source_id(payload.get("source_id", "replay")),
            as_configuration_generation(payload.get("config_generation", 0)),
            payload.get("calibration_profile_id"),
            TimestampQuality(payload.get("timestamp_quality", TimestampQuality.UNKNOWN)),
            tuple(LossReason(value) for value in payload.get("loss_reasons", ())),
        )

    def frame_count_for(self, kind: ReplayKind) -> int:
        return len(self.index.entries_for(kind))

    def entry_at(self, kind: ReplayKind, ordinal: int) -> ReplayIndexEntry:
        entries = self.index.entries_for(kind)
        if not 0 <= ordinal < len(entries):
            raise IndexError("legacy replay ordinal is outside the recording index")
        return entries[ordinal]

    def close(self) -> None:
        self._handle.close()


def _array_from_payload(payload: dict[str, Any]) -> np.ndarray:
    raw = __import__("base64").b64decode(payload["data"])
    return np.frombuffer(raw, dtype=np.dtype(payload["dtype"])).reshape(tuple(payload["shape"]))


def _native_reprocess_state(value: Any) -> str:
    name = str(getattr(value, "name", value)).casefold()
    if name in {"completed", "cancelled", "failed"}:
        return name
    raise RuntimeError(f"native I/Q reprocess returned unexpected state {name}")


def _native_backend_name(value: Any) -> str:
    name = str(getattr(value, "name", value)).casefold()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _legacy_native_processor(requested: str) -> tuple[Any, str, str]:
    """Build the canonical native DSP for the historical Python-owned format."""

    from importlib import import_module

    native = import_module("sdr_monitor._sdr_native")
    requested_name = str(requested).strip().casefold()
    warning = ""
    if requested_name == "cpu":
        processor = native.CpuDspBackend()
        used = "cpu"
    elif requested_name == "cuda":
        try:
            selection = native.DspBackendSelectionOptions(
                native.ComputeBackendKind.CUDA, True, -1, 8
            )
            processor = native.make_dsp_backend(selection)
            used = "cuda"
        except Exception:
            processor = native.CpuDspBackend()
            used = "cpu"
            warning = "CUDA unavailable; reprocess fell back to CPU"
    else:
        raise ValueError("legacy I/Q reprocess requires explicit cpu or cuda backend")
    config = native.DspConfig(
        1024,
        512,
        native.WindowType.HANN,
        native.DetectorType.SAMPLE,
        native.SpectrumUnit.DBFS_BIN,
        native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
        1,
        1,
        8.6,
        native.CalibrationStatus.UNCALIBRATED,
        "",
        5,
    )
    processor.configure(config)
    return processor, used, warning


def _legacy_reprocess_line(frame: Any, backend: str) -> str:
    return json.dumps(
        {
            "sequence": int(frame.frame_sequence),
            "timestamp_ns": int(frame.timestamp_ns),
            "backend": backend,
            "spectrum": np.asarray(frame.values, dtype=np.float64).tolist(),
        },
        separators=(",", ":"),
    ) + "\n"


class ReplayService:
    def __init__(self) -> None:
        self.frame_bus = FrameBus()
        self.clock = ReplayClock()
        self._reader: RecordingReader | NativeSpectrumRecordingReader | None = None
        self._kind = ReplayKind.ALL
        self._frame_count = 0
        self._cursor = 0
        self._state = ReplayState.CLOSED
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-reprocess")
        self._reprocess_future: Future[ReprocessResult] | None = None
        self._cancel_reprocess: threading.Event | None = None
        self._active_native_reprocess: NativeIqReprocessor | None = None
        self._reprocess_generation = 0

    @property
    def index(self) -> RecordingIndex | None:
        return self._reader.index if self._reader is not None else None

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def position(self) -> ReplayPosition:
        with self._lock:
            if self._reader is None or self._frame_count == 0:
                return ReplayPosition(0, 0.0, as_timestamp_ns(0))
            entry = self._reader.entry_at(self._kind, min(self._cursor, self._frame_count - 1))
            return ReplayPosition(
                self._cursor,
                min(self._cursor / max(self._frame_count - 1, 1), 1.0),
                entry.timestamp_ns,
            )

    def open(self, uri: Any, *, kind: ReplayKind = ReplayKind.ALL) -> RecordingIndex:
        with self._lock:
            self.close_replay()
            self._kind = ReplayKind(kind)
            path = Path(uri)
            self._reader = (
                NativeSpectrumRecordingReader(path)
                if is_native_recording_uri(path)
                else RecordingReader(path)
            )
            self._frame_count = self._reader.frame_count_for(self._kind)
            if self._frame_count == 0 and is_native_recording_uri(path):
                self.close_replay()
                raise ValueError(
                    "native capture has no completed Spectrum frames; I/Q replay/reprocess is deferred to R09"
                )
            self._cursor = 0
            self._state = ReplayState.READY
            return self._reader.index

    def seek(self, fraction: float) -> ReplayPosition:
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("replay seek fraction must be between zero and one")
        with self._lock:
            if self._frame_count == 0:
                return self.position
            self._cursor = min(int(fraction * self._frame_count), self._frame_count - 1)
            self._state = ReplayState.PAUSED
            self.clock.pause()
            return self.position

    def set_speed(self, speed: float) -> float:
        return self.clock.set_speed(speed)

    def play(self) -> None:
        with self._lock:
            if self._frame_count:
                self._state = ReplayState.PLAYING
                self.clock.play()

    def pause(self) -> None:
        with self._lock:
            self.clock.pause()
            if self._state is ReplayState.PLAYING:
                self._state = ReplayState.PAUSED

    def read_next(self) -> IQBlock | SpectrumFrame | None:
        with self._lock:
            if self._reader is None or self._cursor >= self._frame_count:
                self._state = ReplayState.COMPLETED
                return None
            entry = self._reader.entry_at(self._kind, self._cursor)
            self._cursor += 1
            frame = self._reader.read(entry)
            if self._cursor >= self._frame_count:
                self._state = ReplayState.COMPLETED
        self.frame_bus.publish(frame)
        return frame

    def tick(self) -> IQBlock | SpectrumFrame | None:
        if self._state is not ReplayState.PLAYING:
            return None
        return self.read_next()

    def reprocess_iq(self, uri: Any, backend: Any, progress: Callable[[float], None] | None = None) -> Future[ReprocessResult]:
        with self._lock:
            if self._reprocess_future is not None and not self._reprocess_future.done():
                raise RuntimeError("an I/Q reprocess is already running")
            self._reprocess_generation += 1
            generation = self._reprocess_generation
            cancel = threading.Event()
            self._cancel_reprocess = cancel
            self._reprocess_future = self._executor.submit(
                self._run_reprocess,
                Path(uri),
                str(backend),
                progress,
                generation,
                cancel,
            )
            return self._reprocess_future

    def cancel_reprocess(self) -> None:
        with self._lock:
            if self._cancel_reprocess is not None:
                self._cancel_reprocess.set()
            if self._active_native_reprocess is not None:
                self._active_native_reprocess.request_cancel()

    def close_replay(self) -> None:
        if self._reader is not None:
            self._reader.close()
        self._reader = None
        self._frame_count = 0
        self._cursor = 0
        self.clock.pause()
        self._state = ReplayState.CLOSED

    def close(self) -> None:
        self.close_replay()
        self.cancel_reprocess()
        with self._lock:
            self._reprocess_generation += 1
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run_reprocess(
        self,
        path: Path,
        requested: str,
        progress: Callable[[float], None] | None,
        generation: int,
        cancel: threading.Event,
    ) -> ReprocessResult:
        if is_native_recording_uri(path):
            return self._run_native_reprocess(path, requested, progress, generation, cancel)
        return self._run_legacy_reprocess(path, requested, progress, generation, cancel)

    def _run_native_reprocess(
        self,
        path: Path,
        requested: str,
        progress: Callable[[float], None] | None,
        generation: int,
        cancel: threading.Event,
    ) -> ReprocessResult:
        reprocessor = NativeIqReprocessor(path, requested)
        with self._lock:
            if generation != self._reprocess_generation:
                reprocessor.request_cancel()
            self._active_native_reprocess = reprocessor
        try:
            if cancel.is_set():
                reprocessor.request_cancel()
            while not reprocessor.process(8):
                if cancel.is_set():
                    reprocessor.request_cancel()
                snapshot = reprocessor.progress
                if progress is not None:
                    progress(snapshot.processed_input_blocks / max(snapshot.total_input_blocks, 1))
            snapshot = reprocessor.progress
            if progress is not None:
                progress(snapshot.processed_input_blocks / max(snapshot.total_input_blocks, 1))
            state = _native_reprocess_state(snapshot.state)
            result = ReprocessResult(
                str(path),
                requested,
                _native_backend_name(snapshot.backend_active),
                state,
                int(snapshot.written_spectrum_frames),
                str(snapshot.output_uri) if state == "completed" else None,
                None,
                str(snapshot.message),
                int(snapshot.input_gap_boundaries),
                int(snapshot.input_gap_samples),
                int(snapshot.discarded_fft_frames),
                generation,
            )
            return self._accept_reprocess_result(result, generation)
        finally:
            with self._lock:
                if self._active_native_reprocess is reprocessor:
                    self._active_native_reprocess = None

    def _run_legacy_reprocess(
        self,
        path: Path,
        requested: str,
        progress: Callable[[float], None] | None,
        generation: int,
        cancel: threading.Event,
    ) -> ReprocessResult:
        """Compatibility bridge: legacy payloads enter the native DSP in blocks.

        New native captures never expose their raw I/Q to Python.  This bridge
        exists solely for the pre-R08 JSON recording format, whose payload was
        historically Python-owned.  It removes the old independent NumPy FFT
        semantic path while retaining physical index traversal for old files.
        """

        reader = RecordingReader(path)
        entries = reader.index.entries_for(ReplayKind.IQ)
        processor, used, warning = _legacy_native_processor(requested)
        processed = 0
        sample_index = 0
        output = path.with_suffix(path.suffix + ".reprocessed.jsonl")
        try:
            with output.open("w", encoding="utf-8") as handle:
                for entry in entries:
                    if cancel.is_set():
                        return self._accept_reprocess_result(
                            ReprocessResult(
                                str(path), requested, used, "cancelled", processed,
                                str(output), warning=warning, generation=generation,
                            ),
                            generation,
                        )
                    block = reader.read(entry)
                    if not isinstance(block, IQBlock):
                        raise ValueError("reprocess index contained a non-IQ recording frame")
                    processor.push_samples(
                        block.samples,
                        float(block.sample_rate_hz),
                        # The pre-R08 legacy schema has no RF centre field.
                        # Keep its compatibility bridge explicit and finite;
                        # canonical native I/Q retains the recorded centre.
                        1.0e6,
                        sample_index,
                    )
                    sample_index += int(block.samples.size)
                    for frame in processor.poll_spectrum(0, False):
                        handle.write(_legacy_reprocess_line(frame, used))
                    processed += 1
                    if progress is not None:
                        progress(processed / max(len(entries), 1))
                for frame in processor.poll_spectrum(0, True):
                    handle.write(_legacy_reprocess_line(frame, used))
        finally:
            reader.close()
        return self._accept_reprocess_result(
            ReprocessResult(
                str(path), requested, used, "completed", processed, str(output), 0.0,
                warning, generation=generation,
            ),
            generation,
        )

    def _accept_reprocess_result(self, result: ReprocessResult, generation: int) -> ReprocessResult:
        with self._lock:
            if generation == self._reprocess_generation:
                return result
        return ReprocessResult(
            result.input_path,
            result.backend_requested,
            result.backend_used,
            "superseded",
            result.frames_processed,
            result.output_path,
            result.max_delta_db,
            "reprocess result superseded by a newer generation",
            result.input_gap_boundaries,
            result.input_gap_samples,
            result.discarded_fft_frames,
            generation,
        )


__all__ = ["RecordingReader", "ReplayService"]
