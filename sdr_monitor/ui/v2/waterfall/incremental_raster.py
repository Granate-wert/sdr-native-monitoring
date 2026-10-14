"""Optional row-revision-driven Waterfall colour cache using ImageItem's mapper."""

from __future__ import annotations

from math import isfinite
import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QImage

from ..spectrum.allocation_budget import PresentationBudgetExceeded
from .bounded_ring import BoundedWaterfallRing
from .protected_storage import is_issued_waterfall_storage_support


_RGBA = QImage.Format.Format_RGBA8888
_RGBA_BYTES_PER_PIXEL = 4
_MAX_REVISION = np.iinfo(np.uint64).max


class WaterfallRasterCache:
    """One accounted RGBA plane shared by both physical spans of one pane."""

    def __init__(self) -> None:
        self._ring = None
        self._budget = None
        self._support = None
        self._rgba = None
        self._seen = None
        self._dirty = None
        self._lut_snapshot = None
        self._mapper = pg.ImageItem(axisOrder="row-major")
        self._last_revision = None
        self._last_policy = None
        self._last_spans = None
        self._refusal_key = None
        self.converted_rows = 0
        self.converted_pixels = 0
        self.converted_runs = 0
        self.reused_rows = 0
        self.fast_render_prepares = 0
        self.fallback_requests = 0

    @property
    def plane(self) -> np.ndarray | None:
        """Borrowed live output for diagnostics/tests; callers must not mutate."""
        return self._rgba

    @property
    def ring(self):
        return self._ring

    def retire(self) -> None:
        """Release pane-owned arrays and mapper scratch without touching old QImages."""
        ring = self._ring
        if ring is not None and ring._protected_storage is not None:
            # Prove creator authority before clearing even optional mapper
            # state; a foreign-thread disposal attempt is a no-op on failure.
            ring.protected_revision
        self._clear_mapper()
        self._rgba = None
        self._seen = None
        self._dirty = None
        self._lut_snapshot = None
        self._ring = None
        self._budget = None
        self._support = None
        self._last_revision = None
        self._last_policy = None
        self._last_spans = None
        self._refusal_key = None

    def render_for(self, item: "WaterfallImageItem") -> QImage | None:
        binding = item._raster_binding
        if binding is None:
            self.fallback_requests += 1
            return None
        (ring, budget, source_tile, actual_image, physical_start, physical_count,
         expected_levels, expected_lut) = binding
        if (item.image is not actual_image or item._raster_cache is not self
                or type(ring) is not BoundedWaterfallRing
                or item.axisOrder != "row-major" or item.autoDownsample is not False
                or item.image is None or type(item.image) is not np.ndarray
                or item.image.dtype != np.float32 or item.image.ndim != 2
                or item.image.shape != (physical_count, ring.columns)
                or type(source_tile) is not np.ndarray
                or source_tile.shape != item.image.shape
                or not self._valid_lut(item.lut) or not self._valid_lut(expected_lut)
                or not np.array_equal(item.lut, expected_lut)
                or not self._valid_levels(item.levels, expected_levels)):
            self.fallback_requests += 1
            return None

        # This native getter enforces creator-thread authority before any
        # optional cache/output state is inspected or mutated.
        native_revision = ring.protected_revision
        if type(native_revision) is not int or native_revision < 0:
            self.fallback_requests += 1
            return None
        if not self._supported_ring(ring, budget):
            self.fallback_requests += 1
            return None

        spans = ring.chronological_spans()
        if ((physical_start, physical_count) not in spans
                or not ring.owns_chronological_tile(source_tile, physical_start, physical_count)
                or not ring.owns_chronological_tile(item.image, physical_start, physical_count)
                or not np.shares_memory(item.image, source_tile)
                or item.image.strides != source_tile.strides):
            self.fallback_requests += 1
            return None
        data = ring.protected_data
        revisions = ring.protected_row_revisions
        if not self._ensure_storage(ring, budget, data, revisions):
            self.fallback_requests += 1
            return None

        lut_bytes = item.lut.tobytes(order="C")
        levels = (float(expected_levels[0]), float(expected_levels[1]))
        policy = (levels, lut_bytes, ring.rows, ring.columns)
        policy_changed = policy != self._last_policy
        spans_changed = spans != self._last_spans
        revision_changed = native_revision != self._last_revision
        lut_changed = (self._last_policy is not None
                      and not np.array_equal(item.lut, self._lut_snapshot))
        if policy_changed or lut_changed:
            for start, count in spans:
                self._dirty[start:start + count] = True
        elif revision_changed or spans_changed:
            self._mark_revision_differences(spans, revisions)
        else:
            self.reused_rows += physical_count

        try:
            self._convert_dirty_runs(spans, data, revisions, levels, item.lut)
        except BaseException:
            self._clear_mapper()
            raise

        self._last_revision = native_revision
        self._last_policy = policy
        self._last_spans = spans
        np.copyto(self._lut_snapshot, item.lut, casting="no")
        image = self._qimage_span(physical_start, physical_count)
        self.fast_render_prepares += 1
        return image

    @staticmethod
    def _valid_levels(levels: object, expected: tuple[float, float]) -> bool:
        if (type(levels) is not np.ndarray or levels.shape != (2,)
                or levels.dtype.kind not in "fi" or not levels.flags.c_contiguous):
            return False
        lower, upper = float(levels[0]), float(levels[1])
        return (isfinite(lower) and isfinite(upper) and lower < upper
                and lower == expected[0] and upper == expected[1])

    @staticmethod
    def _valid_lut(lut: object) -> bool:
        return (type(lut) is np.ndarray and lut.shape == (256, 4)
                and lut.dtype == np.uint8 and lut.flags.c_contiguous)

    @staticmethod
    def _native_owner(array: np.ndarray) -> object | None:
        owner: object = array
        while type(owner) is np.ndarray:
            parent = owner.base
            if parent is None:
                return None
            owner = parent
        while type(owner) is memoryview:
            owner = owner.obj
        return owner

    def _supported_ring(self, ring, budget) -> bool:
        support = getattr(ring, "_protected_support", None)
        if (type(ring) is not BoundedWaterfallRing
                or not is_issued_waterfall_storage_support(support)
                or getattr(ring, "_protected_budget", None) is not budget
                or type(ring._protected_storage) is not support.controller_type
                or type(ring._data) is not np.ndarray
                or ring._data is not ring._protected_data_owner
                or type(ring._protected_revisions) is not np.ndarray
                or ring._protected_revisions is not ring._protected_revision_owner
                or ring._data.dtype != np.float32 or ring._data.shape != (ring.rows, ring.columns)
                or ring._protected_revisions.dtype != np.uint64
                or ring._protected_revisions.shape != (ring.rows,)
                or self._native_owner(ring._data) is None
                or type(self._native_owner(ring._data)) is not support.float_exporter_type
                or type(self._native_owner(ring._protected_revisions)) is not support.revision_exporter_type):
            return False
        self._support = support
        return True

    def _ensure_storage(self, ring, budget, data, revisions) -> bool:
        if self._ring is ring and self._rgba is not None:
            return True
        if self._ring is not None and self._ring is not ring:
            self.retire()
        rows, columns = ring.rows, ring.columns
        payload = rows * columns * _RGBA_BYTES_PER_PIXEL + rows * 8 + rows + 1024
        snapshot = budget.snapshot()
        refusal_key = (ring, budget.limit_bytes, snapshot.observed_bytes, snapshot.reserved_bytes,
                       rows, columns)
        if self._refusal_key == refusal_key:
            return False
        self._refusal_key = None
        reservation = None
        rgba = seen = dirty = lut_snapshot = None
        try:
            reservation = budget.reserve(payload, data, revisions)
            rgba = np.empty((rows, columns, 4), dtype=np.uint8)
            seen = np.full(rows, _MAX_REVISION, dtype=np.uint64)
            dirty = np.zeros(rows, dtype=np.bool_)
            lut_snapshot = np.empty((256, 4), dtype=np.uint8)
            reservation.commit(rgba, seen, dirty, lut_snapshot)
        except (MemoryError, PresentationBudgetExceeded):
            if reservation is not None:
                reservation.close()
            rgba = seen = dirty = lut_snapshot = None
            self._refusal_key = refusal_key
            return False
        except BaseException:
            if reservation is not None:
                reservation.close()
            raise
        self._ring = ring
        self._budget = budget
        self._support = ring._protected_support
        self._rgba, self._seen, self._dirty = rgba, seen, dirty
        self._lut_snapshot = lut_snapshot
        self._refusal_key = None
        return True

    def _mark_revision_differences(self, spans, revisions: np.ndarray) -> None:
        for start, count in spans:
            stop = start + count
            for index in range(start, stop):
                changed = revisions[index] != self._seen[index]
                self._dirty[index] = changed
                if not changed:
                    self.reused_rows += 1

    def _convert_dirty_runs(self, spans, data, revisions, levels, lut) -> None:
        for span_start, span_count in spans:
            stop = span_start + span_count
            index = span_start
            while index < stop:
                if not bool(self._dirty[index]):
                    index += 1
                    continue
                run_start = index
                index += 1
                while index < stop and bool(self._dirty[index]):
                    index += 1
                run_count = index - run_start
                run = data[run_start:index]
                try:
                    self._mapper.setLookupTable(lut)
                    self._mapper.setImage(run, autoLevels=False, levels=levels)
                    self._mapper.render()
                    mapped = self._mapper.qimage
                    if mapped is None:
                        raise RuntimeError("original ImageItem mapper returned no QImage")
                    rgba = mapped.convertToFormat(_RGBA)
                    if rgba.width() != data.shape[1] or rgba.height() != run_count:
                        raise RuntimeError("original ImageItem mapper changed dirty-run dimensions")
                    source = np.frombuffer(rgba.constBits(), dtype=np.uint8)
                    source = source.reshape(run_count, data.shape[1], 4)
                    np.copyto(self._rgba[run_start:index], source, casting="no")
                    np.copyto(self._seen[run_start:index], revisions[run_start:index], casting="no")
                    self._dirty[run_start:index] = False
                    self.converted_rows += run_count
                    self.converted_pixels += run_count * data.shape[1]
                    self.converted_runs += 1
                finally:
                    self._clear_mapper()

    def _qimage_span(self, start: int, count: int) -> QImage:
        view = self._rgba[start:start + count]
        return QImage(view.data, view.shape[1], view.shape[0], int(view.strides[0]), _RGBA)

    def _clear_mapper(self) -> None:
        self._mapper.clear()
        self._mapper.qimage = None
        self._mapper._processingBuffer = None
        self._mapper._displayBuffer = None


class WaterfallImageItem(pg.ImageItem):
    """ImageItem preserving original float image while borrowing mapped RGBA."""

    def __init__(self) -> None:
        self._raster_binding = None
        self._raster_cache = None
        self._last_set_input = None
        self._last_set_actual = None
        self.raster_fast_renders = 0
        self.raster_fallback_renders = 0
        super().__init__(axisOrder="row-major")

    def setImage(self, image=None, *args, **kwargs):
        prior_binding = self._raster_binding
        prior_cache = self._raster_cache
        prior_actual = getattr(self, "image", None)
        reuse_existing = (image is None and prior_binding is not None
                          and not args and set(kwargs).issubset({"autoLevels"})
                          and ("autoLevels" not in kwargs or type(kwargs["autoLevels"]) is bool))
        if reuse_existing:
            result = super().setImage(image, *args, **kwargs)
            if self.image is prior_actual:
                self.qimage = None
                self._processingBuffer = None
                self._displayBuffer = None
                self._raster_binding = prior_binding
                self._raster_cache = prior_cache
                return result
        self._raster_binding = None
        self._raster_cache = None
        self._last_set_input = None
        self._last_set_actual = None
        result = super().setImage(image, *args, **kwargs)
        self.qimage = None
        self._processingBuffer = None
        self._displayBuffer = None
        self._last_set_input = image
        self._last_set_actual = self.image
        return result

    def bind_raster(self, cache: WaterfallRasterCache, ring, source_tile: np.ndarray,
                    physical_start: int, physical_count: int,
                    expected_levels: tuple[float, float], expected_lut: np.ndarray) -> bool:
        if (type(ring) is not BoundedWaterfallRing
                or not ring.owns_chronological_tile(source_tile, physical_start, physical_count)
                or self.image is None or self._last_set_input is not source_tile
                or self.image is not self._last_set_actual
                or type(self.image) is not np.ndarray or self.image.dtype != np.float32
                or self.image.shape != (physical_count, ring.columns)
                or not np.shares_memory(self.image, source_tile)
                or self.image.strides != source_tile.strides):
            self._raster_binding = None
            self._raster_cache = None
            return False
        self._raster_binding = (
            ring, ring._protected_budget if hasattr(ring, "_protected_budget") else None,
            source_tile, self.image, physical_start, physical_count,
            expected_levels, expected_lut,
        )
        self._raster_cache = cache
        self._last_set_input = None
        self._last_set_actual = None
        return True

    def update_raster_policy(self, expected_levels: tuple[float, float],
                             expected_lut: np.ndarray) -> None:
        binding = self._raster_binding
        if binding is not None:
            self._raster_binding = (*binding[:6], expected_levels, expected_lut)

    def clear(self) -> None:
        self._raster_binding = None
        self._raster_cache = None
        self._last_set_input = None
        self._last_set_actual = None
        super().clear()
        self.qimage = None
        self._processingBuffer = None
        self._displayBuffer = None

    def retire_raster_view(self) -> None:
        """Drop only the borrowed RGBA binding, keeping authoritative float rows."""
        self._raster_binding = None
        self._raster_cache = None
        self.qimage = None
        self._processingBuffer = None
        self._displayBuffer = None
        self._renderRequired = True
        self.update()

    def render(self):
        cache = self._raster_cache
        if cache is not None:
            image = cache.render_for(self)
            if image is not None:
                self.qimage = image
                self._processingBuffer = None
                self._displayBuffer = None
                self._renderRequired = False
                self._unrenderable = False
                self.raster_fast_renders += 1
                return
        self.raster_fallback_renders += 1
        return super().render()
