"""Replay index, clock and frame-bus contracts for S09."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable


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
    sequence: int
    timestamp_ns: int


@dataclass(frozen=True, slots=True)
class RecordingIndex:
    path: str
    entries: tuple[ReplayIndexEntry, ...]
    duration_ns: int
    source_size: int

    @property
    def frame_count(self) -> int:
        return len(self.entries)

    def entries_for(self, kind: ReplayKind) -> tuple[ReplayIndexEntry, ...]:
        if kind is ReplayKind.ALL:
            return self.entries
        return tuple(item for item in self.entries if item.kind == kind.value)


@dataclass(frozen=True, slots=True)
class ReplayPosition:
    ordinal: int
    fraction: float
    timestamp_ns: int


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


class FrameBus:
    """Shared publication contract used by live and replay consumers."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Any], None]] = []

    def subscribe(self, callback: Callable[[Any], None]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Any], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, frame: Any) -> None:
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
