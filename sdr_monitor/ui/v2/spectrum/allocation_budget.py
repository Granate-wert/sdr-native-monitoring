"""Shared admission for derived arrays, following actual backing lifetimes.

Source admission precedes UI retention; backend allocation remains independent.
Explicit observe() is accounting only, not admission or eviction.
Reservations cover retained outputs, NOT numerical scratch/native/Qt capacity.
No payload is strongly retained here and no GUI callback runs on root release.
"""
from dataclasses import dataclass
from threading import RLock
import weakref

import numpy as np

from .retained_bytes import retained_roots

DEFAULT_PRESENTATION_BYTES = 256 * 1024 * 1024


class PresentationBudgetExceeded(ValueError):
    """Optional derived presentation allocation denied; not a device failure."""


@dataclass(frozen=True, slots=True)
class AllocationBudgetSnapshot:
    limit_bytes: int
    observed_bytes: int
    reserved_bytes: int
    peak_bytes: int
    rejections: int


class PresentationAllocationBudget:
    """One composition-owned ledger shared across workers and GUI consumers."""

    def __init__(self, limit_bytes: int = DEFAULT_PRESENTATION_BYTES) -> None:
        if isinstance(limit_bytes, bool) or not isinstance(limit_bytes, int) or limit_bytes < 1:
            raise ValueError("presentation allocation budget must be a positive integer")
        self.limit_bytes = limit_bytes
        self._lock = RLock()
        self._roots: dict[int, tuple[int, dict[int, weakref.ReferenceType]]] = {}
        self._observed = self._reserved = self._peak = self._rejections = 0

    def observe(self, *publications: object) -> None:
        """Charge existing source storage even if that puts the ledger over limit."""
        roots = retained_roots(*publications)
        with self._lock:
            self._observe(roots)

    def admit_sources(self, *publications: object) -> bool:
        """Admit before a new UI holder; never register a rejected backing root.

        Sources already exist in the backend/caller. This bounds admitted UI
        roots jointly with derived outputs, not the backend's transient/RSS use.
        Array-free lifecycle notifications always pass, including during pressure.
        """
        roots = retained_roots(*publications)
        if not roots:
            return True
        with self._lock:
            added = sum(max(0, size - self._roots.get(key, (0, {}))[0])
                        for key, (size, _) in roots.items())
            if added and self._observed + self._reserved + added > self.limit_bytes:
                self._rejections += 1
                return False
            self._observe(roots)
            return True

    def _observe(self, roots: dict[int, tuple[int, dict[int, np.ndarray]]]) -> None:
        for key, (size, arrays) in roots.items():
            old_size, aliases = self._roots.get(key, (0, {}))
            for root_id, root in arrays.items():
                if root_id not in aliases:
                    def released(reference, key=key, root_id=root_id):
                        with self._lock:
                            entry = self._roots.get(key)
                            if entry is None or entry[1].get(root_id) is not reference:
                                return
                            del entry[1][root_id]
                            if not entry[1]:
                                self._observed -= entry[0]
                                del self._roots[key]
                    aliases[root_id] = weakref.ref(root, released)
            self._roots[key] = (max(old_size, size), aliases)
            self._observed += max(0, size - old_size)
        self._peak = max(self._peak, self._observed + self._reserved)

    def reserve(self, output_bytes: int, *sources: object) -> "AllocationReservation":
        if isinstance(output_bytes, bool) or not isinstance(output_bytes, int) or output_bytes < 0:
            raise ValueError("output reservation must be a non-negative integer")
        roots = retained_roots(*sources)
        with self._lock:
            self._observe(roots)
            if self._observed + self._reserved + output_bytes > self.limit_bytes:
                self._rejections += 1
                raise PresentationBudgetExceeded("Presentation retained-array allocation budget exceeded")
            self._reserved += output_bytes
            self._peak = max(self._peak, self._observed + self._reserved)
        return AllocationReservation(self, output_bytes)

    def snapshot(self) -> AllocationBudgetSnapshot:
        with self._lock:
            return AllocationBudgetSnapshot(self.limit_bytes, self._observed, self._reserved,
                                            self._peak, self._rejections)


class AllocationReservation:
    def __init__(self, budget: PresentationAllocationBudget, size: int) -> None:
        self._budget, self._size, self._closed = budget, size, False

    def commit(self, *outputs: object) -> None:
        """Exchange reserved bytes for actual roots before publishing outputs."""
        roots = retained_roots(*outputs)
        budget = self._budget
        with budget._lock:
            if self._closed:
                raise RuntimeError("allocation reservation is already closed")
            added = sum(max(0, size - budget._roots.get(key, (0, {}))[0])
                        for key, (size, _) in roots.items())
            if added > self._size:
                raise RuntimeError("derived output exceeded its reserved array bytes")
            budget._reserved -= self._size
            self._closed = True
            budget._observe(roots)

    def close(self) -> None:
        with self._budget._lock:
            if not self._closed:
                self._budget._reserved -= self._size
                self._closed = True

    def __enter__(self) -> "AllocationReservation":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
