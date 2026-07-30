from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Protocol

from .domain import MeasurementSession


class MeasurementRepository(Protocol):
    def add(self, session: MeasurementSession) -> None: ...
    def get(self, session_id: str) -> MeasurementSession: ...
    def remove(self, session_id: str) -> None: ...
    def all(self) -> list[MeasurementSession]: ...


class MemoryMeasurementRepository(MeasurementRepository):
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, MeasurementSession] = OrderedDict()
        self._lock = RLock()

    def add(self, session: MeasurementSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> MeasurementSession:
        with self._lock:
            return self._sessions[session_id]

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def all(self) -> list[MeasurementSession]:
        with self._lock:
            return list(self._sessions.values())

    def find_by_path(self, path: str | Path) -> MeasurementSession | None:
        resolved = Path(path).resolve()
        with self._lock:
            return next((item for item in self._sessions.values() if item.source_path == resolved), None)
