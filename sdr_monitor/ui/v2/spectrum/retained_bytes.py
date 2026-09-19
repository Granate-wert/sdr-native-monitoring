"""Bound exposed array storage, not RSS, native allocator capacity or Qt images.

Walk array-valued fields and nested dataclasses only. Scalar provenance tuples
(including acquisition records) are not traversed on the GUI thread. Callers
explicitly expand collections of array-bearing publications such as traces.
"""
from dataclasses import fields, is_dataclass

import numpy as np


def retained_arrays(*publications: object) -> dict[int, int]:
    """Deduplicate backing allocations, including tiny slices of large arrays.

    O(array fields + owner chain), never O(samples). An opaque native capsule
    is charged by the exposed root array, not its unknowable allocator capacity.
    Independent root arrays sharing a capsule are conservatively charged apart.
    """
    result: dict[int, int] = {}
    seen: set[int] = set()
    pending = list(publications)
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
            if isinstance(base, memoryview):
                base = base.obj
            if isinstance(base, (bytes, bytearray)):
                owner, size = base, len(base)
            elif isinstance(base, np.ndarray):
                pending.append(base)
                continue
            result[id(owner)] = max(result.get(id(owner), 0), size)
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(getattr(value, field.name) for field in fields(value))
    return result


def union_bytes(*allocations: dict[int, int]) -> int:
    merged: dict[int, int] = {}
    for allocation in allocations:
        for key, size in allocation.items():
            merged[key] = max(merged.get(key, 0), size)
    return sum(merged.values())
