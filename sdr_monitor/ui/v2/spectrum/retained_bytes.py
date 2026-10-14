"""Bound exposed array storage, not RSS, native allocator capacity or Qt images.

Walk array-valued fields and nested dataclasses only. Scalar provenance tuples
(including acquisition records) are not traversed on the GUI thread. Callers
explicitly expand collections of array-bearing publications such as traces.
"""
from dataclasses import fields, is_dataclass
from typing import Hashable

import numpy as np


def retained_arrays(*publications: object, support=None) -> dict[Hashable, int]:
    """Deduplicate backing allocations, including tiny slices of large arrays.

    O(array fields + owner chain), never O(samples). An opaque native capsule
    is charged by the exposed root array, not its unknowable allocator capacity.
    Independent root arrays sharing a capsule are conservatively charged apart.
    """
    return {key: size for key, (size, _) in retained_roots(*publications, support=support).items()}


def retained_roots(*publications: object, support=None) -> dict[Hashable, tuple[int, dict[int, object]]]:
    """Same backing accounting, with temporary root references for weak tracking."""
    if support is not None:
        # Waterfall's package imports this ledger while defining its ring.
        # Resolve the optional proof only after initialization, never for legacy.
        from ..waterfall.protected_storage import is_issued_waterfall_storage_support

        if not is_issued_waterfall_storage_support(support):
            support = None
    result: dict[Hashable, tuple[int, dict[int, object]]] = {}
    seen: set[int] = set()
    pending = list(publications)

    def add_exporter(exporter: object) -> bool:
        if support is None or type(exporter) not in (
                support.float_exporter_type, support.revision_exporter_type):
            return False
        size = exporter.allocation_nbytes
        allocation_id = exporter.allocation_id
        if type(size) is not int or size < 0 or type(allocation_id) is not int or allocation_id < 1:
            raise ValueError("native Waterfall exporter returned invalid allocation metadata")
        key = (type(exporter), allocation_id)
        old_size, aliases = result.get(key, (0, {}))
        aliases[id(exporter)] = exporter
        result[key] = (max(old_size, size), aliases)
        return True

    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, np.ndarray):
            if value.dtype.hasobject:
                raise ValueError("object arrays have no bounded numeric storage contract")
            root = value
            while isinstance(root.base, np.ndarray):
                root = root.base
            owner: object = root
            size = int(root.nbytes)
            base = root.base
            while isinstance(base, memoryview):
                base = base.obj
            if isinstance(base, np.ndarray):
                pending.append(base)
                continue
            if add_exporter(base):
                continue
            if isinstance(base, (bytes, bytearray)):
                owner, size = base, len(base)
            old_size, aliases = result.get(id(owner), (0, {}))
            aliases[id(root)] = root
            result[id(owner)] = (max(old_size, size), aliases)
        elif add_exporter(value):
            continue
        elif isinstance(value, memoryview):
            if support is None:
                continue
            owner = value.obj
            while isinstance(owner, memoryview):
                owner = owner.obj
            if not add_exporter(owner) and isinstance(owner, np.ndarray):
                pending.append(owner)
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(getattr(value, field.name) for field in fields(value))
    return result


def union_bytes(*allocations: dict[Hashable, int]) -> int:
    merged: dict[Hashable, int] = {}
    for allocation in allocations:
        for key, size in allocation.items():
            merged[key] = max(merged.get(key, 0), size)
    return sum(merged.values())
