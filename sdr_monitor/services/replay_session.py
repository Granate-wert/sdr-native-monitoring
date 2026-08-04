"""Indexed recording reader and asynchronous replay/reprocess service."""

from __future__ import annotations

import json
import struct
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..domain import FrameBus, IQBlock, RecordingIndex, ReplayClock, ReplayIndexEntry, ReplayKind, ReplayPosition, ReplayState, ReprocessResult, SpectrumFrame


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
                entries.append(ReplayIndexEntry(len(entries), offset, length + 4, str(item["kind"]), int(item.get("sequence", 0)), int(item.get("timestamp_ns", 0))))
        self.index = RecordingIndex(str(self.path), tuple(entries), max((item.timestamp_ns for item in entries), default=0) - min((item.timestamp_ns for item in entries), default=0), self.path.stat().st_size)

    def read(self, entry: ReplayIndexEntry) -> Any:
        self._handle.seek(entry.offset)
        raw_length = self._handle.read(4)
        length = struct.unpack("<I", raw_length)[0]
        payload = json.loads(self._handle.read(length).decode("utf-8"))
        if payload["kind"] == ReplayKind.IQ.value:
            samples = _array_from_payload(payload["samples"])
            return IQBlock(int(payload["sequence"]), int(payload["timestamp_ns"]), samples, float(payload["sample_rate_hz"]), str(payload.get("source_id", "replay")), int(payload.get("config_generation", 0)))
        return SpectrumFrame(int(payload["sequence"]), int(payload["timestamp_ns"]), _array_from_payload(payload["frequencies_hz"]), _array_from_payload(payload["values"]), str(payload.get("unit", "dBFS/bin")), str(payload.get("source_id", "replay")), int(payload.get("config_generation", 0)), payload.get("calibration_profile_id"))

    def close(self) -> None:
        self._handle.close()


def _array_from_payload(payload: dict[str, Any]) -> np.ndarray:
    raw = __import__("base64").b64decode(payload["data"])
    return np.frombuffer(raw, dtype=np.dtype(payload["dtype"])).reshape(tuple(payload["shape"]))


class ReplayService:
    def __init__(self) -> None:
        self.frame_bus = FrameBus()
        self.clock = ReplayClock()
        self._reader: RecordingReader | None = None
        self._kind = ReplayKind.ALL
        self._entries: tuple[ReplayIndexEntry, ...] = ()
        self._cursor = 0
        self._state = ReplayState.CLOSED
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-reprocess")
        self._reprocess_future: Future[ReprocessResult] | None = None
        self._cancel_reprocess = threading.Event()

    @property
    def index(self) -> RecordingIndex | None:
        return self._reader.index if self._reader is not None else None

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def position(self) -> ReplayPosition:
        with self._lock:
            if not self._entries:
                return ReplayPosition(0, 0.0, 0)
            entry = self._entries[min(self._cursor, len(self._entries) - 1)]
            return ReplayPosition(self._cursor, min(self._cursor / max(len(self._entries) - 1, 1), 1.0), entry.timestamp_ns)
    def open(self, uri: Any, *, kind: ReplayKind = ReplayKind.ALL) -> RecordingIndex:
        with self._lock:
            self.close_replay()
            self._reader = RecordingReader(Path(uri))
            self._kind = ReplayKind(kind)
            self._entries = self._reader.index.entries_for(self._kind)
            self._cursor = 0
            self._state = ReplayState.READY
            return self._reader.index

    def seek(self, fraction: float) -> ReplayPosition:
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("replay seek fraction must be between zero and one")
        with self._lock:
            if not self._entries:
                return self.position
            self._cursor = min(int(fraction * len(self._entries)), len(self._entries) - 1)
            self._state = ReplayState.PAUSED
            self.clock.pause()
            return self.position

    def set_speed(self, speed: float) -> float:
        return self.clock.set_speed(speed)

    def play(self) -> None:
        with self._lock:
            if self._entries:
                self._state = ReplayState.PLAYING
                self.clock.play()

    def pause(self) -> None:
        with self._lock:
            self.clock.pause()
            if self._state is ReplayState.PLAYING:
                self._state = ReplayState.PAUSED

    def read_next(self) -> Any | None:
        with self._lock:
            if self._reader is None or self._cursor >= len(self._entries):
                self._state = ReplayState.COMPLETED
                return None
            entry = self._entries[self._cursor]
            self._cursor += 1
            frame = self._reader.read(entry)
            if self._cursor >= len(self._entries):
                self._state = ReplayState.COMPLETED
        self.frame_bus.publish(frame)
        return frame

    def tick(self) -> Any | None:
        if self._state is not ReplayState.PLAYING:
            return None
        return self.read_next()

    def reprocess_iq(self, uri: Any, backend: Any, progress: Callable[[float], None] | None = None) -> Future[ReprocessResult]:
        self._cancel_reprocess.clear()
        self._reprocess_future = self._executor.submit(self._run_reprocess, Path(uri), str(backend), progress)
        return self._reprocess_future

    def cancel_reprocess(self) -> None:
        self._cancel_reprocess.set()

    def close_replay(self) -> None:
        if self._reader is not None:
            self._reader.close()
        self._reader = None
        self._entries = ()
        self._cursor = 0
        self.clock.pause()
        self._state = ReplayState.CLOSED

    def close(self) -> None:
        self.close_replay()
        self.cancel_reprocess()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_reprocess(self, path: Path, requested: str, progress: Callable[[float], None] | None) -> ReprocessResult:
        reader = RecordingReader(path)
        entries = reader.index.entries_for(ReplayKind.IQ)
        used = requested.lower()
        warning = ""
        if used == "cuda":
            used = "cpu"
            warning = "CUDA unavailable; reprocess fell back to CPU"
        processed = 0
        output = path.with_suffix(path.suffix + ".reprocessed.jsonl")
        try:
            with output.open("w", encoding="utf-8") as handle:
                for entry in entries:
                    if self._cancel_reprocess.is_set():
                        return ReprocessResult(str(path), requested, used, "cancelled", processed, str(output), warning=warning)
                    block = reader.read(entry)
                    spectrum = np.abs(np.fft.fft(block.samples)).astype(np.float64)
                    handle.write(json.dumps({"sequence": block.sequence, "timestamp_ns": block.timestamp_ns, "backend": used, "spectrum": spectrum.tolist()}, separators=(",", ":")) + "\n")
                    processed += 1
                    if progress is not None:
                        progress(processed / max(len(entries), 1))
        finally:
            reader.close()
        return ReprocessResult(str(path), requested, used, "completed", processed, str(output), 0.0, warning)


__all__ = ["RecordingReader", "ReplayService"]
