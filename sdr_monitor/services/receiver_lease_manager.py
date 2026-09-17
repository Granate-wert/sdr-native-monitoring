"""Bounded, Qt-free ownership contract for future receiver coordinators.

R10-E0 intentionally does not replace the existing single-Live/Sweep lease.
This manager has no native dependency and never opens an IIO context; it only
makes future resource-keyed admission deterministic and testable.
"""

from __future__ import annotations

import threading

from ..domain.receiver_topology import AcquisitionGroup


class ReceiverLease:
    """One idempotent control-plane lease; it contains no SDR handle."""

    __slots__ = ("_group", "_manager", "_released")

    def __init__(self, manager: "ReceiverLeaseManager", group: AcquisitionGroup) -> None:
        self._manager = manager
        self._group = group
        self._released = False

    @property
    def group(self) -> AcquisitionGroup:
        return self._group

    def release(self) -> None:
        if not self._released:
            self._manager._release(self._group)
            self._released = True

    def __enter__(self) -> "ReceiverLease":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release()


class ReceiverLeaseManager:
    """Admit at most one acquisition group per physical stream resource."""

    def __init__(self, *, max_active_resources: int = 4) -> None:
        if isinstance(max_active_resources, bool) or not isinstance(max_active_resources, int) or max_active_resources < 1:
            raise ValueError("max active receiver resources must be a positive integer")
        self._max_active_resources = max_active_resources
        self._active: dict[str, AcquisitionGroup] = {}
        self._lock = threading.RLock()

    @property
    def active_resource_count(self) -> int:
        with self._lock:
            return len(self._active)

    def acquire(self, group: AcquisitionGroup) -> ReceiverLease:
        """Claim an exact resource before E3 creates its native coordinator."""

        with self._lock:
            occupied = self._active.get(group.physical_stream_resource_id)
            if occupied is not None:
                raise RuntimeError(
                    "physical stream resource is already leased by acquisition group "
                    f"{occupied.group_id}"
                )
            if len(self._active) >= self._max_active_resources:
                raise RuntimeError("bounded receiver lease capacity is exhausted")
            self._active[group.physical_stream_resource_id] = group
        return ReceiverLease(self, group)

    def _release(self, group: AcquisitionGroup) -> None:
        with self._lock:
            if self._active.get(group.physical_stream_resource_id) == group:
                del self._active[group.physical_stream_resource_id]


__all__ = ["ReceiverLease", "ReceiverLeaseManager"]
