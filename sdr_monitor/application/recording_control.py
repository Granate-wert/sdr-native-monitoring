"""Qt-free use cases for standalone recording control operations."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol

from ..domain import RecordingHealth, RecordingOptions, RecordingResult


class RecordingControlPort(Protocol):
    """Infrastructure operations required by the bounded recording-control use case."""

    def start(self, options: RecordingOptions) -> None: ...
    def start_now(self, options: RecordingOptions) -> None: ...
    def stop(self, timeout_s: float = 5.0) -> RecordingResult: ...
    def health(self) -> RecordingHealth: ...
    def recover_partial(self, uri: str | Path) -> Mapping[str, object]: ...
    def close(self) -> None: ...


class RecordingControlUseCases(Protocol):
    """Recording control operations consumed by the Qt presenter."""

    def start(self, options: RecordingOptions) -> RecordingHealth: ...
    def start_now(self, options: RecordingOptions) -> RecordingHealth: ...
    def stop(self, timeout_s: float = 5.0) -> RecordingResult: ...
    def health(self) -> RecordingHealth: ...
    def recover_partial(self, uri: str | Path) -> Mapping[str, object]: ...
    def shutdown(self) -> None: ...


class RecordingControlApplicationService:
    """Coordinates recording control without Qt, widgets or storage internals."""

    def __init__(self, port: RecordingControlPort) -> None:
        self._port = port

    def start(self, options: RecordingOptions) -> RecordingHealth:
        self._port.start(options)
        return self._port.health()

    def start_now(self, options: RecordingOptions) -> RecordingHealth:
        """Start immediately only when the selected infrastructure supports it.

        Native RTBW uses a visible controlled Live restart; the historical
        framed writer deliberately rejects this operation rather than creating
        a second hidden recording path.
        """

        self._port.start_now(options)
        return self._port.health()

    def stop(self, timeout_s: float = 5.0) -> RecordingResult:
        return self._port.stop(timeout_s)

    def health(self) -> RecordingHealth:
        return self._port.health()

    def recover_partial(self, uri: str | Path) -> Mapping[str, object]:
        return self._port.recover_partial(uri)

    def shutdown(self) -> None:
        self._port.close()


__all__ = [
    "RecordingControlApplicationService",
    "RecordingControlPort",
    "RecordingControlUseCases",
]
