"""Replay index, clock and frame-bus contracts for S09."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from .identity import FrameSequence, TimestampNs, as_frame_sequence, as_timestamp_ns
from .recording import RecordedFrame


class ReplayKind(StrEnum):
    IQ = "iq"
    SPECTRUM = "spectrum"
    ALL = "all"


class ReplayState(StrEnum):
    CLOSED = "closed"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOP_TIMEOUT = "stop_timeout"


@dataclass(frozen=True, slots=True)
class ReplayIndexEntry:
    ordinal: int
    offset: int
    size: int
    kind: str
    sequence: FrameSequence
    timestamp_ns: TimestampNs

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.offset < 0 or self.size < 0:
            raise ValueError("replay index offsets must not be negative")
        object.__setattr__(self, "sequence", as_frame_sequence(self.sequence))
        object.__setattr__(self, "timestamp_ns", as_timestamp_ns(self.timestamp_ns))


@dataclass(frozen=True, slots=True)
class RecordingIndex:
    path: str
    entries: tuple[ReplayIndexEntry, ...]
    duration_ns: int
    source_size: int
    # Native R08-D spectrum replay keeps its physical on-disk index in C++ as
    # a bounded sparse index.  The Python/UI contract therefore receives a
    # scalar frame-count hint instead of retaining an unbounded entry tuple.
    frame_count_hint: int = 0
    recording_format: str = "legacy_sdrrec"
    native_iq_available: bool = False
    native_spectrum_available: bool = False
    control_gap_count: int = 0
    control_gap_duration_ns: int = 0

    @property
    def frame_count(self) -> int:
        return max(len(self.entries), self.frame_count_hint)

    def entries_for(self, kind: ReplayKind) -> tuple[ReplayIndexEntry, ...]:
        if kind is ReplayKind.ALL:
            return self.entries
        return tuple(item for item in self.entries if item.kind == kind.value)


@dataclass(frozen=True, slots=True)
class ReplayPosition:
    ordinal: int
    fraction: float
    timestamp_ns: TimestampNs

    def __post_init__(self) -> None:
        if self.ordinal < 0 or not 0.0 <= self.fraction <= 1.0:
            raise ValueError("invalid replay position")
        object.__setattr__(self, "timestamp_ns", as_timestamp_ns(self.timestamp_ns))


@dataclass(frozen=True, slots=True)
class ReprocessResult:
    input_path: str
    backend_requested: str
    backend_used: str
    status: str
    frames_processed: int
    output_path: str | None = None
    max_delta_db: float | None = None
    warning: str = ""
    # R09 appends bounded native reprocess provenance.  These are counts from
    # the physical final I/Q index, not GUI frames or a hidden retry backlog.
    input_gap_boundaries: int = 0
    input_gap_samples: int = 0
    discarded_fft_frames: int = 0
    generation: int = 0


class FrameBus:
    """Shared publication contract used by live and replay consumers."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[RecordedFrame], None]] = []

    def subscribe(self, callback: Callable[[RecordedFrame], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[RecordedFrame], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, frame: RecordedFrame) -> None:
        for callback in tuple(self._subscribers):
            callback(frame)


class ReplayClock:
    def __init__(self) -> None:
        self.speed = 1.0
        self.playing = False
        self._last_tick = time.monotonic()

    def set_speed(self, speed: float) -> float:
        if not 0.25 <= speed <= 8.0:
            raise ValueError("replay speed must be between 0.25x and 8x")
        self.speed = speed
        self._last_tick = time.monotonic()
        return speed

    def play(self) -> None:
        self.playing = True
        self._last_tick = time.monotonic()

    def pause(self) -> None:
        self.playing = False

    def elapsed_scaled(self) -> float:
        now = time.monotonic()
        elapsed = now - self._last_tick
        self._last_tick = now
        return elapsed * self.speed if self.playing else 0.0


__all__ = ["FrameBus", "RecordingIndex", "ReplayClock", "ReplayIndexEntry", "ReplayKind", "ReplayPosition", "ReplayState", "ReprocessResult"]
