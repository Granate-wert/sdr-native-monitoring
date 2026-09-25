"""Pure worker-side density display preparation; no Qt, device or executor owner.

The GUI retains the last accepted immutable image. Visual requests borrow that
image read-only and produce a distinct owned image for new updates; exact display
rematerialization may reuse that immutable image. Cancelled/obsolete work never
mutates the accepted accumulator. Results do not retain a chain of old histories.
The caller must bump policy revision on measurement/epoch reset as well as mode
or transfer changes, and reject stale source/policy/history before image upload.
"""
import hashlib
import importlib
import weakref
from dataclasses import dataclass, field
from functools import cache
from typing import Callable

import numpy as np

from .cancellation import CancelCheck, check_cancelled
from .persistence_contracts import (
    DensityValueMode,
    PersistenceDensityView,
    PersistenceRenderMode,
    map_density_row_for_display,
)

IMAGE_BATCH = 65_536
# An internal provenance hint, not a security boundary: arbitrary Python
# histories need not contain finite display-domain values for the native path.
_WORKER_IMAGE_TOKEN = object()


@cache
def _visual_smoothing_kernel() -> Callable[[np.ndarray, np.ndarray, np.ndarray], None] | None:
    """Use the bounded native row kernel when this release actually includes it."""
    try:
        native = importlib.import_module("sdr_monitor._sdr_native")
    except (ImportError, OSError):
        return None
    return getattr(native, "_visual_smooth_row_into", None)


@dataclass(frozen=True, slots=True)
class PersistenceInputWitness:
    """Weak publication witness plus content digest; never pins input arrays.

    Identity alone is not freshness. Hash all values/edges in bounded chunks;
    rematerialization requires both the same publication array AND same bytes.
    """
    density: weakref.ReferenceType[np.ndarray]
    digest: bytes


def persistence_input_witness(view: PersistenceDensityView, *,
                              cancelled: CancelCheck = None) -> PersistenceInputWitness:
    digest = hashlib.sha256()
    digest.update(repr((view.value_mode.value, view.level_unit)).encode("utf-8"))
    for array in (view.density, view.frequency_edges_hz, view.level_edges):
        digest.update(repr((array.shape, array.dtype.str)).encode("ascii"))
        rows = array if array.ndim == 2 else (array,)
        for row in rows:
            for first in range(0, row.size, IMAGE_BATCH):
                check_cancelled(cancelled)
                chunk = row[first:first + IMAGE_BATCH]
                if chunk.flags.c_contiguous:
                    digest.update(memoryview(chunk).cast("B"))
                else:
                    digest.update(chunk.tobytes())  # at most IMAGE_BATCH cells, no retained copy
    check_cancelled(cancelled)
    return PersistenceInputWitness(weakref.ref(view.density), digest.digest())


def same_persistence_input(left: PersistenceInputWitness | None,
                           right: PersistenceInputWitness) -> bool:
    return (left is not None and left.density() is not None
            and left.density() is right.density() and left.digest == right.digest)


def persistence_witness_scratch(view: PersistenceDensityView) -> int:
    """Maximum transient byte copy for a noncontiguous hashing chunk."""
    return max(min(IMAGE_BATCH, array.shape[-1]) * array.dtype.itemsize
               for array in (view.density, view.frequency_edges_hz, view.level_edges))


@dataclass(frozen=True, slots=True)
class PersistenceImagePolicy:
    revision: int
    mode: PersistenceRenderMode
    logarithmic: bool

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("persistence policy revision must be a nonnegative integer")
        if not isinstance(self.mode, PersistenceRenderMode) or not isinstance(self.logarithmic, bool):
            raise ValueError("persistence image policy must be explicit")


@dataclass(frozen=True, slots=True)
class PersistenceImageHistory:
    revision: int
    policy: PersistenceImagePolicy
    physical_rect: tuple[float, float, float, float]
    value_mode: DensityValueMode
    level_unit: str
    image: np.ndarray
    input_witness: PersistenceInputWitness | None = None
    count_maximum: float = 0.0
    _worker_image_ref: weakref.ReferenceType[np.ndarray] | None = field(
        default=None, repr=False, compare=False)
    _worker_image_token: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("persistence history revision must be a nonnegative integer")
        _validate_image(self.image)


@dataclass(frozen=True, slots=True)
class PersistenceImageRequest:
    view: PersistenceDensityView
    policy: PersistenceImagePolicy
    history: PersistenceImageHistory | None = None
    rematerialize: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.rematerialize, bool):
            raise ValueError("rematerialization intent must be boolean")
        # Validated density publications are immutable across worker handoff.
        # Read-only is not a numerical validation certificate or identity cache.
        if any(array.flags.writeable for array in
               (self.view.density, self.view.frequency_edges_hz, self.view.level_edges)):
            raise ValueError("worker density projection requires immutable input arrays")

    @property
    def history_revision(self) -> int | None:
        return None if self.history is None else self.history.revision


@dataclass(frozen=True, slots=True)
class PreparedPersistenceImage:
    view: PersistenceDensityView
    policy: PersistenceImagePolicy
    base_history_revision: int | None
    image: np.ndarray
    count_maximum: float = 0.0
    input_witness: PersistenceInputWitness | None = None
    rematerialize: bool = False
    _worker_image_ref: weakref.ReferenceType[np.ndarray] | None = field(
        default=None, repr=False, compare=False)
    _worker_image_token: object | None = field(default=None, repr=False, compare=False)

    @property
    def quantitative_labels(self) -> tuple[str, str]:
        if self.view.value_mode is DensityValueMode.PROBABILITY:
            return ("0.000 probability", "1.000 probability")
        return ("0 count", f"{self.count_maximum:.0f} count")

    def __post_init__(self) -> None:
        _validate_image(self.image)
        if self.image.shape != self.view.density.shape:
            raise ValueError("persistence image must preserve the full density geometry")

    def matches(self, request: PersistenceImageRequest) -> bool:
        """Exact request witness for GUI admission, including smoothing base."""
        return (self.view is request.view and self.policy == request.policy
                and self.rematerialize == request.rematerialize
                and self.base_history_revision == request.history_revision)

    def as_history(self, revision: int) -> PersistenceImageHistory:
        # Only this image/geometry/policy survive. Never retain the prior request
        # or source density in smoothing history across subsequent publications.
        return PersistenceImageHistory(revision, self.policy, self.view.physical_rect,
                                       self.view.value_mode, self.view.level_unit, self.image,
                                       self.input_witness, self.count_maximum,
                                       self._worker_image_ref, self._worker_image_token)


def _validate_image(image: np.ndarray) -> None:
    if (image.ndim != 2 or image.size == 0 or image.dtype != np.float32
            or image.flags.writeable or not image.flags.owndata or not image.flags.c_contiguous):
        raise ValueError("prepared persistence image must be owned read-only C float32")


def persistence_image_reserve(request: PersistenceImageRequest) -> int:
    """Output plus bounded NumPy mapping/reduction scratch, not Qt/native RSS.

    Inputs/accepted history are accounted separately by the shared root ledger.
    Scratch: finite selection (input itemsize+bool), or Visual mapped image,
    reusable smoothing mask and mapping finite mask (6 bytes/cell with history),
    each at most IMAGE_BATCH cells. A batched C layout charges the full batch.
    """
    density = request.view.density
    batch = min(IMAGE_BATCH, density.size if _flat_mapping_eligible(density) else density.shape[1])
    hash_scratch = (persistence_witness_scratch(request.view)
                    if request.policy.mode is PersistenceRenderMode.VISUAL else 0)
    mapping_bytes_per_cell = max(6 if request.history is not None else 5,
                                 density.dtype.itemsize + 1)
    return int(density.size * 4 + max(batch * mapping_bytes_per_cell, hash_scratch))


def _flat_mapping_eligible(density: np.ndarray) -> bool:
    """Batch adjacent C rows without changing the 64K-cell cancellation bound.

    Short rows can otherwise multiply Python mapping/native smoothing calls by
    the histogram height. Tiny fixtures and rows already >= one batch retain
    their old path and scratch envelope.
    """
    # A subclass may override reshape() with a full copy despite C flags.
    return (type(density) is np.ndarray and density.flags.c_contiguous and density.size >= IMAGE_BATCH
            and 1024 <= density.shape[1] < IMAGE_BATCH)


def _compatible_history(request: PersistenceImageRequest) -> np.ndarray | None:
    history, view = request.history, request.view
    if (request.policy.mode is PersistenceRenderMode.VISUAL and history is not None
            and history.policy == request.policy and history.image.shape == view.density.shape
            and history.physical_rect == view.physical_rect
            and history.value_mode is view.value_mode and history.level_unit == view.level_unit):
        return history.image
    return None


def _trusted_worker_history(request: PersistenceImageRequest, image: np.ndarray | None) -> bool:
    history = request.history
    return (image is not None and history is not None
            and history._worker_image_token is _WORKER_IMAGE_TOKEN
            and history._worker_image_ref is not None
            and history._worker_image_ref() is image)


def prepare_persistence_image(request: PersistenceImageRequest, *,
                              cancelled: CancelCheck = None) -> PreparedPersistenceImage:
    check_cancelled(cancelled)
    view = request.view
    witness = (persistence_input_witness(view, cancelled=cancelled)
               if request.policy.mode is PersistenceRenderMode.VISUAL else None)
    history = _compatible_history(request)
    trusted_history = _trusted_worker_history(request, history)
    if (request.rematerialize and history is not None and request.history is not None and witness is not None
            and same_persistence_input(request.history.input_witness, witness)):
        return PreparedPersistenceImage(view, request.policy, request.history_revision, history,
                                        request.history.count_maximum, witness, True,
                                        (weakref.ref(history) if trusted_history else None),
                                        (_WORKER_IMAGE_TOKEN if trusted_history else None))
    maximum = 0.0
    if view.value_mode is DensityValueMode.COUNT:
        # Same global finite maximum as the GUI transfer without a full-matrix
        # finite selection. Cancellation remains bounded by one chunk.
        for row in view.density:
            for first in range(0, row.size, IMAGE_BATCH):
                check_cancelled(cancelled)
                chunk = row[first:first + IMAGE_BATCH]
                finite = chunk[np.isfinite(chunk)]
                if finite.size:
                    maximum = max(maximum, float(np.max(finite)))
                # Drop this allocation before evaluating the next selection;
                # assignment would otherwise overlap old/new finite buffers.
                del finite
        # Do not keep the last reduction scratch alive during image mapping.
        del chunk
    image = np.empty(view.density.shape, dtype=np.float32)
    flat_mapping = (_flat_mapping_eligible(view.density)
                    and (history is None or type(history) is np.ndarray))
    mapping_batch = min(IMAGE_BATCH, view.density.size if flat_mapping else image.shape[1])
    scratch = (None if history is None else
               np.empty(mapping_batch, dtype=np.float32))
    mask = (None if history is None else
            np.empty(mapping_batch, dtype=np.bool_))
    native_smoothing = _visual_smoothing_kernel() if trusted_history else None
    source_rows = (view.density.reshape(-1),) if flat_mapping else view.density
    target_rows = (image.reshape(-1),) if flat_mapping else image
    history_rows = ((history.reshape(-1),) if flat_mapping else history) if history is not None else None
    for row_index, source_row in enumerate(source_rows):
        for first in range(0, source_row.size, IMAGE_BATCH):
            check_cancelled(cancelled)
            source = source_row[first:first + IMAGE_BATCH]
            target = target_rows[row_index][first:first + IMAGE_BATCH]
            mapped = target if scratch is None else scratch[:source.size]
            if source.flags.c_contiguous and source[0] == 0.0 and not np.any(source):
                # All measured cells are signed zero: transfer is identity in
                # every mode. Avoid finite-mask/fill/clip passes, not cells or
                # validation. Nonfinite chunks cannot enter this branch. Visual
                # decay below must still execute against the accepted history.
                np.copyto(mapped, source)
            else:
                map_density_row_for_display(source, value_mode=view.value_mode,
                    logarithmic=request.policy.logarithmic, count_maximum=maximum, out=mapped)
            if history is not None:
                assert history_rows is not None
                old = history_rows[row_index][first:first + IMAGE_BATCH]
                if native_smoothing is not None:
                    native_smoothing(mapped, old, target)
                else:
                    assert mask is not None
                    row_mask = mask[:source.size]
                    np.subtract(mapped, old, out=target)
                    np.multiply(target, .18, out=target)
                    np.greater_equal(mapped, old, out=row_mask)
                    np.multiply(target, .65 / .18, out=target, where=row_mask)
                    np.add(old, target, out=target)
    check_cancelled(cancelled)
    image.setflags(write=False)
    return PreparedPersistenceImage(view, request.policy, request.history_revision, image, maximum,
                                    witness, request.rematerialize,
                                    (weakref.ref(image) if history is None or trusted_history else None),
                                    (_WORKER_IMAGE_TOKEN if history is None or trusted_history else None))


class PersistenceImageSteps:
    """Experimental bounded continuation of the exact optional image work.

    ``advance`` does at most ``max_chunks`` 64K-cell hash/reduction/mapping
    chunks and returns only a complete image. It owns no executor or Qt
    callback. A future scheduler must hold one instance and its existing
    ``persistence_image_reserve`` reservation until completion or cancellation.
    Stop/Hide/policy change must signal cooperative cancellation; ``close()``
    may run only after the active worker acknowledges, never concurrently with
    ``advance()``. The normal product path above is deliberately unchanged
    while that ownership is evaluated.
    """

    def __init__(self, request: PersistenceImageRequest) -> None:
        self.request = request
        self._history = _compatible_history(request)
        self._trusted_history = _trusted_worker_history(request, self._history)
        self._witness: PersistenceInputWitness | None = None
        self._digest = hashlib.sha256() if request.policy.mode is PersistenceRenderMode.VISUAL else None
        if self._digest is not None:
            self._digest.update(repr((request.view.value_mode.value, request.view.level_unit)).encode("utf-8"))
        self._hash_array = self._hash_row = self._hash_offset = 0
        self._hash_meta_pending = True
        self._count_row = self._count_offset = 0
        self._map_row = self._map_offset = 0
        self._maximum = 0.0
        self._image: np.ndarray | None = None
        self._scratch: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self._native_smoothing: Callable[[np.ndarray, np.ndarray, np.ndarray], None] | None = None
        self._stage = ("witness" if self._digest is not None else
                       "count" if request.view.value_mode is DensityValueMode.COUNT else "map")

    def advance(self, *, max_chunks: int = 8, cancelled: CancelCheck = None) -> PreparedPersistenceImage | None:
        """Continue without publishing a partially hashed or mapped image."""
        if isinstance(max_chunks, bool) or not isinstance(max_chunks, int) or max_chunks < 1:
            raise ValueError("max_chunks must be a positive integer")
        if self._stage in ("complete", "aborted"):
            raise RuntimeError("terminal persistence continuation cannot publish twice")
        try:
            return self._advance(max_chunks=max_chunks, cancelled=cancelled)
        except BaseException:
            # A failed chunk may have already advanced a cursor or allocated
            # only part of its scratch. Retrying that state is never safe.
            self.close()
            raise

    def close(self) -> None:
        """Discard partial work after worker acknowledgement, not concurrently."""
        if self._stage not in ("complete", "aborted"):
            self._stage = "aborted"
        self._discard_payload()

    def _discard_payload(self) -> None:
        # The caller receives the completed image separately. Do not let a
        # paused or terminal stepper pin old history/source arrays.
        if hasattr(self, "request"):
            del self.request
        self._history = self._witness = self._digest = self._image = None
        self._scratch = self._mask = self._native_smoothing = None

    def _complete(self, result: PreparedPersistenceImage) -> PreparedPersistenceImage:
        self._stage = "complete"
        self._discard_payload()
        return result

    def _advance(self, *, max_chunks: int, cancelled: CancelCheck) -> PreparedPersistenceImage | None:
        check_cancelled(cancelled)
        remaining = max_chunks
        while remaining:
            if self._stage == "witness":
                self._hash_chunk()
                remaining -= 1
                if self._hash_array == 3:
                    assert self._digest is not None
                    self._witness = PersistenceInputWitness(
                        weakref.ref(self.request.view.density), self._digest.digest())
                    self._digest = None
                    history = self._history
                    if (self.request.rematerialize and history is not None
                            and self.request.history is not None
                            and same_persistence_input(self.request.history.input_witness, self._witness)):
                        check_cancelled(cancelled)
                        return self._complete(PreparedPersistenceImage(
                            self.request.view, self.request.policy, self.request.history_revision,
                            history, self.request.history.count_maximum, self._witness, True,
                            (weakref.ref(history) if self._trusted_history else None),
                            (_WORKER_IMAGE_TOKEN if self._trusted_history else None)))
                    self._stage = ("count" if self.request.view.value_mode is DensityValueMode.COUNT
                                   else "map")
            elif self._stage == "count":
                self._count_chunk()
                remaining -= 1
                if self._count_row == self.request.view.density.shape[0]:
                    self._stage = "map"
            else:
                self._map_chunk()
                remaining -= 1
                if self._map_row == self.request.view.density.shape[0]:
                    check_cancelled(cancelled)
                    assert self._image is not None
                    self._image.setflags(write=False)
                    return self._complete(PreparedPersistenceImage(
                        self.request.view, self.request.policy, self.request.history_revision,
                        self._image, self._maximum, self._witness, self.request.rematerialize,
                        (weakref.ref(self._image) if self._history is None or self._trusted_history else None),
                        (_WORKER_IMAGE_TOKEN if self._history is None or self._trusted_history else None)))
            check_cancelled(cancelled)
        return None

    def _hash_chunk(self) -> None:
        assert self._digest is not None
        arrays = (self.request.view.density, self.request.view.frequency_edges_hz,
                  self.request.view.level_edges)
        array = arrays[self._hash_array]
        if self._hash_meta_pending:
            self._digest.update(repr((array.shape, array.dtype.str)).encode("ascii"))
            self._hash_meta_pending = False
        row = array[self._hash_row] if array.ndim == 2 else array
        chunk = row[self._hash_offset:self._hash_offset + IMAGE_BATCH]
        if chunk.flags.c_contiguous:
            self._digest.update(memoryview(chunk).cast("B"))
        else:
            self._digest.update(chunk.tobytes())
        self._hash_offset += chunk.size
        if self._hash_offset == row.size:
            self._hash_offset = 0
            self._hash_row += 1
            if self._hash_row == (array.shape[0] if array.ndim == 2 else 1):
                self._hash_row = 0
                self._hash_array += 1
                self._hash_meta_pending = True

    def _count_chunk(self) -> None:
        rows = self.request.view.density
        row = rows[self._count_row]
        chunk = row[self._count_offset:self._count_offset + IMAGE_BATCH]
        finite = chunk[np.isfinite(chunk)]
        if finite.size:
            self._maximum = max(self._maximum, float(np.max(finite)))
        self._count_offset += chunk.size
        if self._count_offset == row.size:
            self._count_offset = 0
            self._count_row += 1

    def _map_chunk(self) -> None:
        if self._image is None:
            self._image = np.empty(self.request.view.density.shape, dtype=np.float32)
            if self._history is not None:
                batch = min(IMAGE_BATCH, self._image.shape[1])
                self._scratch = np.empty(batch, dtype=np.float32)
                self._mask = np.empty(batch, dtype=np.bool_)
                self._native_smoothing = _visual_smoothing_kernel() if self._trusted_history else None
        row = self.request.view.density[self._map_row]
        source = row[self._map_offset:self._map_offset + IMAGE_BATCH]
        target = self._image[self._map_row, self._map_offset:self._map_offset + IMAGE_BATCH]
        mapped = target if self._scratch is None else self._scratch[:source.size]
        if source.flags.c_contiguous and source[0] == 0.0 and not np.any(source):
            np.copyto(mapped, source)
        else:
            map_density_row_for_display(source, value_mode=self.request.view.value_mode,
                logarithmic=self.request.policy.logarithmic, count_maximum=self._maximum, out=mapped)
        if self._history is not None:
            old = self._history[self._map_row, self._map_offset:self._map_offset + IMAGE_BATCH]
            if self._native_smoothing is not None:
                self._native_smoothing(mapped, old, target)
            else:
                assert self._mask is not None
                row_mask = self._mask[:source.size]
                np.subtract(mapped, old, out=target)
                np.multiply(target, .18, out=target)
                np.greater_equal(mapped, old, out=row_mask)
                np.multiply(target, .65 / .18, out=target, where=row_mask)
                np.add(old, target, out=target)
        self._map_offset += source.size
        if self._map_offset == row.size:
            self._map_offset = 0
            self._map_row += 1
