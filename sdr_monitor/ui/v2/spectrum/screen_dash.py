"""Bounded high-contrast history strokes on a noisy, dense spectrum.

Qt's dashed-path stroker has a severe paint-time cost on a noisy, dense
envelope. AVERAGE uses screen-X solid dash fragments, intentionally different
from Qt's arc-length phase. MAXIMUM and MINIMUM use source-exact finite edges
with their original dotted and dash-dot Qt pens; the pen phase restarts per
edge on noisy contours, retaining substantially more visible peak evidence
than an X-only mask. Smooth dense contours retain Qt's continuous native
pattern so those roles do not collapse to a solid line.
There is no retained geometry across zoom, resize, DPR or new data.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLineF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen, QTransform

try:
    from pyqtgraph.graphicsItems.PlotCurveItem import OpenGLHelpers
    from pyqtgraph.Qt.internals import PrimitiveArray
except ImportError:  # Defensive fallback for an unsupported runtime, never a release speed claim.
    OpenGLHelpers = None
    PrimitiveArray = None

_MAX_INPUT_POINTS = 65_536
_MAX_DASH_SEGMENTS = 65_536
_MAX_CUTS = 131_072

_SUPPORTED_STYLES = (Qt.PenStyle.DashLine, Qt.PenStyle.DotLine,
                     Qt.PenStyle.DashDotLine)


def screen_dash_segments(
    x: np.ndarray,
    y: np.ndarray,
    transform: QTransform,
    *,
    on_pixels: float,
    off_pixels: float,
) -> np.ndarray | None:
    """Return finite, bounded local-coordinate lines or ``None`` on admission denial.

    Dashes are anchored to physical screen X; each finite source run is split
    separately. Every vertex is a cut, so no returned line can bridge a
    vertex, let alone a missing sample. Unsupported non-horizontal transforms
    fall back to Qt's native dashed path instead of drawing misleading data.
    """

    if (
        x.ndim != 1 or y.ndim != 1 or len(x) != len(y)
        or len(x) > _MAX_INPUT_POINTS or not transform.isAffine()
        or not math.isfinite(on_pixels) or not math.isfinite(off_pixels)
        or on_pixels <= 0.0 or off_pixels <= 0.0
    ):
        return None
    if len(x) < 2:
        return np.empty((0, 4), dtype=np.float64)
    scale_x = transform.m11()
    offset_x = transform.dx()
    if (not all(math.isfinite(value) for value in (
            scale_x, transform.m12(), transform.m21(), transform.m22(), offset_x, transform.dy()))
            or scale_x <= 0.0 or transform.m12() != 0.0 or transform.m21() != 0.0):
        return None

    finite = np.isfinite(x) & np.isfinite(y)
    starts = np.flatnonzero(finite & ~np.r_[False, finite[:-1]])
    stops = np.flatnonzero(finite & ~np.r_[finite[1:], False]) + 1
    period = on_pixels + off_pixels
    parts: list[np.ndarray] = []
    total_segments = 0
    for start, stop in zip(starts, stops, strict=True):
        if stop - start < 2:
            continue
        rx = np.asarray(x[start:stop], dtype=np.float64)
        ry = np.asarray(y[start:stop], dtype=np.float64)
        screen_x = scale_x * rx + offset_x
        if not np.all(np.isfinite(screen_x)) or np.any(np.diff(screen_x) <= 0.0):
            return None
        first_cycle = math.floor(screen_x[0] / period)
        last_cycle = math.ceil(screen_x[-1] / period)
        cycles = last_cycle - first_cycle + 1
        if cycles * 2 + len(screen_x) > _MAX_CUTS:
            return None
        dash_starts = np.arange(first_cycle, last_cycle + 1, dtype=np.float64) * period
        cuts = np.concatenate((screen_x, dash_starts, dash_starts + on_pixels))
        cuts = cuts[(cuts >= screen_x[0]) & (cuts <= screen_x[-1])]
        cuts.sort()
        cuts = np.unique(cuts)
        mids = (cuts[:-1] + cuts[1:]) * 0.5
        edge = np.searchsorted(screen_x, mids, side="right") - 1
        selected = (
            (edge < len(rx) - 1)
            & (np.mod(mids, period) < on_pixels)
            & (cuts[1:] > cuts[:-1] + 1e-9)
        )
        first = cuts[:-1][selected]
        last = cuts[1:][selected]
        edge = edge[selected]
        total_segments += len(edge)
        if total_segments > _MAX_DASH_SEGMENTS:
            return None
        if not len(edge):
            continue
        dx = np.diff(rx)
        dy = np.diff(ry)
        t0 = (first - screen_x[edge]) / (screen_x[edge + 1] - screen_x[edge])
        t1 = (last - screen_x[edge]) / (screen_x[edge + 1] - screen_x[edge])
        parts.append(np.stack((
            rx[edge] + dx[edge] * t0,
            ry[edge] + dy[edge] * t0,
            rx[edge] + dx[edge] * t1,
            ry[edge] + dy[edge] * t1,
        ), axis=1))
    if not parts:
        return np.empty((0, 4), dtype=np.float64)
    result = np.concatenate(parts)
    return result if np.all(np.isfinite(result)) else None


def finite_source_edges(x: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    """Return source-exact adjacent edges, never connecting across missing data."""

    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or len(x) > _MAX_INPUT_POINTS:
        return None
    if len(x) < 2:
        return np.empty((0, 4), dtype=np.float64)
    finite = np.isfinite(x) & np.isfinite(y)
    joined = finite[:-1] & finite[1:]
    result = np.stack((x[:-1][joined], y[:-1][joined],
                       x[1:][joined], y[1:][joined]), axis=1)
    return np.asarray(result, dtype=np.float64)


def _partition_history_edges(
    x: np.ndarray, y: np.ndarray, transform: QTransform,
    style: Qt.PenStyle, width_pixels: float,
) -> tuple[np.ndarray, QPainterPath] | None:
    """Separate short-run native paths from long, source-exact pen edges.

    The decision is per source edge, not per whole frame: a noisy change cannot
    switch an unchanged flat plateau from dots into a solid line. Every short
    run becomes a native Qt subpath with continuous local dash phase. Long
    noisy edges keep their exact source vertices and use one batched drawLines.
    ``None`` means the full native Qt path is required or already optimal.
    """

    if (not transform.isAffine() or transform.m12() != 0.0
            or transform.m21() != 0.0 or not all(math.isfinite(value)
                for value in (transform.m11(), transform.m22(), width_pixels))
            or width_pixels <= 0.0):
        return None
    edges = finite_source_edges(x, y)
    if edges is None or len(edges) == 0:
        return None
    finite = np.isfinite(x) & np.isfinite(y)
    edge_indices = np.flatnonzero(finite[:-1] & finite[1:])
    lengths = np.hypot((edges[:, 2] - edges[:, 0]) * transform.m11(),
                       (edges[:, 3] - edges[:, 1]) * transform.m22())
    if not np.all(np.isfinite(lengths)):
        return None
    on_units = 2.0 if style == Qt.PenStyle.DotLine else 5.0
    short = lengths <= on_units * width_pixels
    if np.all(short):
        return None  # The existing cached continuous Qt path is exact and cheap.
    # Isolated short edges occupy less than a few pixels: keep them in the
    # batched line call. Only a contiguous smooth span can visibly collapse
    # into a solid line or justify the cost of a native subpath.
    smooth = np.zeros(len(edges), dtype=np.bool_)
    short_positions = np.flatnonzero(short)
    if len(short_positions):
        breaks = np.flatnonzero(np.diff(edge_indices[short_positions]) != 1) + 1
        starts = np.r_[0, breaks]
        stops = np.r_[breaks, len(short_positions)]
        for start, stop in zip(starts, stops, strict=True):
            if stop - start >= 8:
                smooth[short_positions[start:stop]] = True
    path = QPainterPath()
    previous = -2
    for edge_index in edge_indices[smooth]:
        index = int(edge_index)
        if index != previous + 1:
            path.moveTo(float(x[index]), float(y[index]))
        path.lineTo(float(x[index + 1]), float(y[index + 1]))
        previous = index
    return edges[~smooth], path


class ScreenDashCurveItem(pg.PlotCurveItem):
    """Use fast solid fragments for supported high-contrast history pens."""

    def paint(self, painter: QPainter, option: object, widget: object) -> None:
        pen = self.opts["pen"]
        if (PrimitiveArray is None or OpenGLHelpers is None or pen is None
                or pen.style() not in _SUPPORTED_STYLES or not pen.isCosmetic()
                or self.opts["connect"] != "finite" or self.opts["fillLevel"] is not None
                or self.opts["shadowPen"] is not None or self.opts["stepMode"]
                or self.opts["compositionMode"] is not None or self._exportOpts is not False
                or isinstance(widget, OpenGLHelpers.GraphicsViewGLWidget)):
            super().paint(painter, option, widget)
            return
        x, y = self.getData()
        if x is None or y is None or len(x) < 2:
            return
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        style = pen.style()
        path: QPainterPath | None = None
        if style == Qt.PenStyle.DashLine:
            dash_width = max(1.0, float(pen.widthF())) * dpr
            segments = screen_dash_segments(x, y, painter.deviceTransform(),
                                            on_pixels=8.0 * dash_width,
                                            off_pixels=4.0 * dash_width)
        else:
            partition = _partition_history_edges(
                x, y, painter.deviceTransform(), style,
                max(1.0, float(pen.widthF())) * dpr)
            if partition is None:
                super().paint(painter, option, widget)
                return
            segments, path = partition
        if segments is None:
            # Unsupported geometry must remain correct, even if slower.
            super().paint(painter, option, widget)
            return
        if not len(segments) and (path is None or path.isEmpty()):
            return
        lines = None
        if len(segments):
            lines = PrimitiveArray(QLineF, 4)
            lines.resize(len(segments))
            lines.ndarray()[:] = segments
        stroke = QPen(pen)
        if style == Qt.PenStyle.DashLine:
            stroke.setStyle(Qt.PenStyle.SolidLine)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, bool(self.opts["antialias"]))
            painter.setPen(stroke)
            if lines is not None:
                painter.drawLines(*lines.drawargs())
            if path is not None and not path.isEmpty():
                painter.drawPath(path)
        finally:
            painter.restore()


class ScreenDashPlotDataItem(pg.PlotDataItem):
    """PlotDataItem with a bounded history-pattern curve painter."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        original: pg.PlotCurveItem = self.curve  # type: ignore[has-type]  # pyqtgraph is untyped
        # 0.13.7 does not connect this child signal; disconnecting it emits a
        # PySide warning. The old child cannot be clicked after detachment and
        # Qt drops any existing connection when deleteLater() destroys it.
        original.setParentItem(None)
        original.deleteLater()
        self.curve = ScreenDashCurveItem()
        self.curve.setParentItem(self)
        self.curve.sigClicked.connect(self.sigClicked)
        self.updateItems(styleUpdate=True)
