"""Qt-free use cases for indexed replay control and reprocessing.

The application boundary intentionally preserves the existing S09 reader,
physical byte-offset seek, ``FrameBus`` publication and reprocess semantics.
It only prevents a Qt presenter from owning an infrastructure service or
calling that service on the widget thread.
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Protocol

from ..domain import RecordedFrame, RecordingIndex, ReplayKind, ReplayPosition, ReprocessResult


class ReplayControlPort(Protocol):
    """Infrastructure operations required by the replay-control use case."""

    @property
    def position(self) -> ReplayPosition: ...

    def open(self, uri: str | Path, *, kind: ReplayKind = ReplayKind.ALL) -> RecordingIndex: ...
    def seek(self, fraction: float) -> ReplayPosition: ...
    def set_speed(self, speed: float) -> float: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def read_next(self) -> RecordedFrame | None: ...
    def reprocess_iq(self, uri: str | Path, backend: str) -> Future[ReprocessResult]: ...
    def cancel_reprocess(self) -> None: ...
    def close(self) -> None: ...


class ReplayControlUseCases(Protocol):
    """Replay operations consumed by the Qt presenter."""

    def open(self, uri: str | Path, kind: ReplayKind = ReplayKind.ALL) -> RecordingIndex: ...
    def seek(self, fraction: float) -> ReplayPosition: ...
    def set_speed(self, speed: float) -> ReplayPosition: ...
    def play(self) -> ReplayPosition: ...
    def pause(self) -> ReplayPosition: ...
    def read_next(self) -> tuple[RecordedFrame | None, ReplayPosition]: ...
    def start_reprocess(self, uri: str | Path, backend: str) -> Future[ReprocessResult]: ...
    def cancel_reprocess(self) -> None: ...
    def shutdown(self) -> None: ...


class ReplayControlApplicationService:
    """Coordinates replay control without Qt, widgets or reader internals."""

    def __init__(self, port: ReplayControlPort) -> None:
        self._port = port

    def open(self, uri: str | Path, kind: ReplayKind = ReplayKind.ALL) -> RecordingIndex:
        return self._port.open(uri, kind=kind)

    def seek(self, fraction: float) -> ReplayPosition:
        return self._port.seek(fraction)

    def set_speed(self, speed: float) -> ReplayPosition:
        self._port.set_speed(speed)
        return self._port.position

    def play(self) -> ReplayPosition:
        self._port.play()
        return self._port.position

    def pause(self) -> ReplayPosition:
        self._port.pause()
        return self._port.position

    def read_next(self) -> tuple[RecordedFrame | None, ReplayPosition]:
        return self._port.read_next(), self._port.position

    def start_reprocess(self, uri: str | Path, backend: str) -> Future[ReprocessResult]:
        return self._port.reprocess_iq(uri, backend)

    def cancel_reprocess(self) -> None:
        self._port.cancel_reprocess()

    def shutdown(self) -> None:
        self._port.close()


__all__ = [
    "ReplayControlApplicationService",
    "ReplayControlPort",
    "ReplayControlUseCases",
]
