"""Pure worker-side density display preparation; no Qt, device or executor owner.

The GUI retains the last accepted immutable image. Visual requests borrow that
image read-only and produce a distinct owned image; cancelled/obsolete work never
mutates the accepted accumulator. Results do not retain a chain of old histories.
The caller must bump policy revision on measurement/epoch reset as well as mode
or transfer changes, and reject stale source/policy/history before image upload.
"""
from dataclasses import dataclass

import numpy as np

from .cancellation import CancelCheck, check_cancelled
from .persistence_contracts import (
    DensityValueMode, PersistenceDensityView, PersistenceRenderMode,
    map_density_row_for_display,
)

IMAGE_BATCH = 65_536


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

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("persistence history revision must be a nonnegative integer")
        _validate_image(self.image)


@dataclass(frozen=True, slots=True)
class PersistenceImageRequest:
    view: PersistenceDensityView
    policy: PersistenceImagePolicy
    history: PersistenceImageHistory | None = None

    def __post_init__(self) -> None:
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
                and self.base_history_revision == request.history_revision)

    def as_history(self, revision: int) -> PersistenceImageHistory:
        # Only this image/geometry/policy survive. Never retain the prior request
        # or source density in smoothing history across subsequent publications.
        return PersistenceImageHistory(revision, self.policy, self.view.physical_rect,
                                       self.view.value_mode, self.view.level_unit, self.image)


def _validate_image(image: np.ndarray) -> None:
    if (image.ndim != 2 or image.size == 0 or image.dtype != np.float32
            or image.flags.writeable or not image.flags.owndata or not image.flags.c_contiguous):
        raise ValueError("prepared persistence image must be owned read-only C float32")


def persistence_image_reserve(request: PersistenceImageRequest) -> int:
    """Output plus bounded NumPy mapping/reduction scratch, not Qt/native RSS.

    Inputs/accepted history are accounted separately by the shared root ledger.
    Scratch: finite selection (input itemsize+bool), or Visual mapped/delta/mask/
    indexed multiplication (13 bytes/cell), each at most IMAGE_BATCH cells.
    """
    density = request.view.density
    batch = min(IMAGE_BATCH, density.shape[1])
    return int(density.size * 4 + batch * max(13, density.dtype.itemsize + 1))


def _compatible_history(request: PersistenceImageRequest) -> np.ndarray | None:
    history, view = request.history, request.view
    if (request.policy.mode is PersistenceRenderMode.VISUAL and history is not None
            and history.policy == request.policy and history.image.shape == view.density.shape
            and history.physical_rect == view.physical_rect
            and history.value_mode is view.value_mode and history.level_unit == view.level_unit):
        return history.image
    return None


def prepare_persistence_image(request: PersistenceImageRequest, *,
                              cancelled: CancelCheck = None) -> PreparedPersistenceImage:
    check_cancelled(cancelled)
    view = request.view
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
    history = _compatible_history(request)
    scratch = (None if history is None else
               np.empty(min(IMAGE_BATCH, image.shape[1]), dtype=np.float32))
    for row_index, source_row in enumerate(view.density):
        for first in range(0, source_row.size, IMAGE_BATCH):
            check_cancelled(cancelled)
            source = source_row[first:first + IMAGE_BATCH]
            target = image[row_index, first:first + IMAGE_BATCH]
            mapped = target if scratch is None else scratch[:source.size]
            map_density_row_for_display(source, value_mode=view.value_mode,
                logarithmic=request.policy.logarithmic, count_maximum=maximum, out=mapped)
            if history is not None:
                old = history[row_index, first:first + IMAGE_BATCH]
                delta = mapped - old
                delta *= .18
                delta[mapped >= old] *= .65 / .18
                np.add(old, delta, out=target)
    check_cancelled(cancelled)
    image.setflags(write=False)
    return PreparedPersistenceImage(view, request.policy, request.history_revision, image, maximum)
