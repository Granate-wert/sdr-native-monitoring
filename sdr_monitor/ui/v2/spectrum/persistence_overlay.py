"""Identity-aware ImageItem persistence layer for the shared SpectrumScene ViewBox."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from contextlib import nullcontext
from time import monotonic_ns

import numpy as np
import pyqtgraph as pg
from .allocation_budget import PresentationAllocationBudget, PresentationBudgetExceeded
from PySide6.QtCore import QRectF, QTimer

from .persistence_contracts import (
    DensityValueMode,
    PersistenceDensityView,
    PersistenceRenderMode,
    inferno_lookup_table,
    map_density_for_display,
    map_density_row_for_display,
)


@dataclass(frozen=True, slots=True)
class PersistenceOverlayMetrics:
    """Scalar UI delivery evidence; it never claims native persistence quality."""

    image_uploads: int = 0
    level_only_updates: int = 0
    identity_uploads_suppressed: int = 0
    cadence_uploads_deferred: int = 0
    hidden_updates: int = 0
    retained_extra_image_buffers: int = 0
    allocation_denials: int = 0


class PersistenceOverlay:
    """Own one ImageItem below traces and markers in an externally owned ViewBox."""

    def __init__(self, plot_item: pg.PlotItem, *, z_value: int, image_cadence_hz: float = 15.0) -> None:
        if image_cadence_hz <= 0.0:
            raise ValueError("persistence image cadence must be positive")
        self._plot_item = plot_item
        self._interval_ns = int(1_000_000_000 / image_cadence_hz)
        self._image = pg.ImageItem(axisOrder="row-major")
        self._image.setZValue(z_value)
        self._image.setLookupTable(inferno_lookup_table())
        self._image.setVisible(False)
        self._plot_item.addItem(self._image)
        self._visible = True
        self._presentation_active = True
        self._logarithmic = True
        self._render_mode = PersistenceRenderMode.DIRECT
        self._mapping_dirty = False
        self._latest_view: PersistenceDensityView | None = None
        self._pending_view: PersistenceDensityView | None = None
        self._uploaded_density: np.ndarray | None = None
        self._visual_buffer: np.ndarray | None = None
        self._row_scratch: np.ndarray | None = None
        self._last_upload_ns: int | None = None
        self._metrics = PersistenceOverlayMetrics()
        self.allocation_budget: PresentationAllocationBudget | None = None
        self.allocation_limited = False
        self.on_budget_changed: Callable[[], None] | None = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.flush_pending)

    @property
    def image_item(self) -> pg.ImageItem:
        return self._image

    @property
    def metrics(self) -> PersistenceOverlayMetrics:
        return self._metrics

    @property
    def render_mode(self) -> PersistenceRenderMode:
        return self._render_mode

    @property
    def logarithmic(self) -> bool:
        return self._logarithmic

    @property
    def latest_view(self) -> PersistenceDensityView | None:
        return self._latest_view

    def set_visible(self, visible: bool) -> None:
        became_visible = bool(visible) and not self._visible
        self._visible = bool(visible)
        self._image.setVisible(self._visible and self._uploaded_density is not None)
        if not self._visible:
            self._timer.stop()
        elif (self._presentation_active and self._latest_view is not None
              and (self._mapping_dirty or (became_visible and self.allocation_limited))):
            # An explicit off/on action may recover the stopped latest frame
            # after another owner releases capacity. Repeated True requests
            # must not become an unbounded budget retry loop.
            self._discard_pending()
            self._upload(self._latest_view, now_ns=monotonic_ns(), force=True)
        elif self._visible:
            self.flush_pending()

    def set_presentation_active(self, active: bool) -> None:
        """Transient page visibility; preserve the user's density toggle."""
        active = bool(active)
        if active == self._presentation_active:
            return
        self._presentation_active = active
        if not active:
            self._timer.stop()
            self._uploaded_density = None
            self._image.clear()
            self._image.setVisible(False)
            # Keep smoothing history if requested, but Direct has no visual
            # history to preserve. Latest/pending analytical views stay intact.
            self._set_metrics(retained_extra_image_buffers=int(self._visual_buffer is not None))
        elif self._visible and self._latest_view is not None:
            self._discard_pending()
            self._upload(self._latest_view, now_ns=monotonic_ns(), force=True)

    def set_logarithmic(self, logarithmic: bool) -> None:
        if self._logarithmic == bool(logarithmic):
            return
        self._logarithmic = bool(logarithmic)
        self._visual_buffer = None
        self._row_scratch = None
        self._mapping_dirty = True
        if self._latest_view is not None:
            self._upload(self._latest_view, now_ns=monotonic_ns(), force=True)

    def set_render_mode(self, mode: PersistenceRenderMode) -> None:
        if self._render_mode is mode:
            return
        self._render_mode = mode
        self._visual_buffer = None
        self._row_scratch = None
        self._mapping_dirty = True
        if self._latest_view is not None:
            self._upload(self._latest_view, now_ns=monotonic_ns(), force=True)

    def set_frame(self, view: PersistenceDensityView, *, now_ns: int | None = None) -> None:
        if self.allocation_budget is not None:
            self.allocation_budget.observe(view)
        self._latest_view = view
        now = monotonic_ns() if now_ns is None else now_ns
        if not self._visible or not self._presentation_active:
            self._pending_view = view
            self._set_metrics(hidden_updates=self._metrics.hidden_updates + 1)
            return
        if view.density is self._uploaded_density and not self._mapping_dirty:
            self._discard_pending()
            self._set_metrics(identity_uploads_suppressed=self._metrics.identity_uploads_suppressed + 1)
            return
        if self._last_upload_ns is not None and now - self._last_upload_ns < self._interval_ns:
            self._pending_view = view
            self._set_metrics(cadence_uploads_deferred=self._metrics.cadence_uploads_deferred + 1)
            self._schedule_pending(now)
            return
        self._discard_pending()
        self._upload(view, now_ns=now)

    def flush_pending(self, now_ns: int | None = None) -> None:
        view = self._pending_view
        if view is None or not self._visible or not self._presentation_active:
            return
        now = monotonic_ns() if now_ns is None else now_ns
        if self._last_upload_ns is not None and now - self._last_upload_ns < self._interval_ns:
            self._schedule_pending(now)
            return
        self._pending_view = None
        self._upload(view, now_ns=now)

    def set_level_window(self, lower: float, upper: float) -> None:
        """Change ImageItem levels only; source density is neither recopied nor reset."""

        if not 0.0 <= lower < upper <= 1.0:
            raise ValueError("persistence display levels must be within [0, 1]")
        self._image.setLevels((lower, upper))
        self._set_metrics(level_only_updates=self._metrics.level_only_updates + 1)

    def clear_local_image(self) -> None:
        """Clear only the presentation layer; no native accumulation/reset command exists."""

        self._discard_pending()
        self._latest_view = None
        self._uploaded_density = None
        self._visual_buffer = None
        self._row_scratch = None
        self._mapping_dirty = False
        self._image.clear()
        self._image.setVisible(False)
        self._set_metrics(retained_extra_image_buffers=0)
        self._set_allocation_limited(False)

    def _upload(self, view: PersistenceDensityView, *, now_ns: int, force: bool = False) -> None:
        if not self._visible or not self._presentation_active:
            return
        if not force and view.density is self._uploaded_density and not self._mapping_dirty:
            return
        reuse = (self._render_mode is PersistenceRenderMode.VISUAL and self._visual_buffer is not None
                 and self._visual_buffer.shape == view.density.shape)
        if not reuse:
            # Incompatible smoothing history has no meaning on a changed
            # density geometry. Do not pin it across repeated budget refusals.
            self._visual_buffer = None
            self._row_scratch = None
        reserve = 0 if reuse else int(view.density.size * 4)
        if reuse and (self._row_scratch is None or self._row_scratch.size != view.density.shape[1]):
            reserve += int(view.density.shape[1] * 4)
        try:
            allocation = (nullcontext(None) if self.allocation_budget is None else
                          self.allocation_budget.reserve(reserve, view))
            with allocation as ticket:
                image = self._render_image(view)
                if ticket is not None:
                    ticket.commit(image, self._visual_buffer, self._row_scratch)
        except PresentationBudgetExceeded:
            # Do not mislabel an old image as the current measurement. Keep
            # latest and smoothing intent, but never queue a retry timer here.
            self._discard_pending()
            self._uploaded_density = None
            self._image.clear()
            self._image.setVisible(False)
            self._set_metrics(allocation_denials=self._metrics.allocation_denials + 1,
                              retained_extra_image_buffers=int(self._visual_buffer is not None))
            self._set_allocation_limited(True)
            return
        left, bottom, width, height = view.physical_rect
        self._image.setImage(image, autoLevels=False, levels=(0.0, 1.0))
        self._image.setRect(QRectF(left, bottom, width, height))
        self._image.setVisible(True)
        self._uploaded_density = view.density
        self._mapping_dirty = False
        self._last_upload_ns = now_ns
        self._set_allocation_limited(False)
        self._set_metrics(
            image_uploads=self._metrics.image_uploads + 1,
            retained_extra_image_buffers=1,
        )

    def _render_image(self, view: PersistenceDensityView) -> np.ndarray:
        if self._render_mode is PersistenceRenderMode.DIRECT:
            self._visual_buffer = None
            return map_density_for_display(view, logarithmic=self._logarithmic)
        if self._visual_buffer is None or self._visual_buffer.shape != view.density.shape:
            self._visual_buffer = map_density_for_display(view, logarithmic=self._logarithmic)
            return self._visual_buffer
        attack = 0.65
        release = 0.18
        scratch = self._row_scratch_for(view.density.shape[1])
        count_maximum = _count_maximum(view)
        for index, source_row in enumerate(view.density):
            map_density_row_for_display(
                source_row,
                value_mode=view.value_mode,
                logarithmic=self._logarithmic,
                count_maximum=count_maximum,
                out=scratch,
            )
            delta = scratch - self._visual_buffer[index]
            delta *= release
            delta[scratch >= self._visual_buffer[index]] *= attack / release
            self._visual_buffer[index] += delta
        return self._visual_buffer

    def _row_scratch_for(self, width: int) -> np.ndarray:
        if self._row_scratch is None or self._row_scratch.size != width:
            self._row_scratch = np.empty(width, dtype=np.float32)
        return self._row_scratch

    def _schedule_pending(self, now_ns: int) -> None:
        if self._last_upload_ns is None:
            return
        delay_ms = max(1, int((self._interval_ns - (now_ns - self._last_upload_ns)) / 1_000_000))
        if not self._timer.isActive():
            self._timer.start(delay_ms)

    def _discard_pending(self) -> None:
        """Prevent a stale cadence timer from uploading an older density."""

        self._pending_view = None
        self._timer.stop()

    def _set_metrics(self, **updates: int) -> None:
        self._metrics = PersistenceOverlayMetrics(
            image_uploads=updates.get("image_uploads", self._metrics.image_uploads),
            level_only_updates=updates.get("level_only_updates", self._metrics.level_only_updates),
            identity_uploads_suppressed=updates.get(
                "identity_uploads_suppressed", self._metrics.identity_uploads_suppressed
            ),
            cadence_uploads_deferred=updates.get(
                "cadence_uploads_deferred", self._metrics.cadence_uploads_deferred
            ),
            hidden_updates=updates.get("hidden_updates", self._metrics.hidden_updates),
            retained_extra_image_buffers=updates.get(
                "retained_extra_image_buffers", self._metrics.retained_extra_image_buffers
            ),
            allocation_denials=updates.get("allocation_denials", self._metrics.allocation_denials),
        )

    def _set_allocation_limited(self, limited: bool) -> None:
        if self.allocation_limited != limited:
            self.allocation_limited = limited
            if self.on_budget_changed is not None:
                self.on_budget_changed()


def _count_maximum(view: PersistenceDensityView) -> float:
    if view.value_mode is not DensityValueMode.COUNT:
        return 1.0
    finite = view.density[np.isfinite(view.density)]
    return 0.0 if finite.size == 0 else float(np.max(finite))
