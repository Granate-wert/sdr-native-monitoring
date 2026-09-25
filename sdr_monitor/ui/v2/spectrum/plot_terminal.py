"""Retry-safe terminal retirement of a pyqtgraph PlotItem.

pyqtgraph 0.14.0 clears ``ctrlMenu`` early in ``PlotItem.close()`` and then
uses its absence to skip subsequent calls. If an axis or ViewBox removal
raises, a normal retry therefore needs to finish the remaining Qt items.
This helper is only for an acknowledged shell shutdown, never Live Stop.
"""

from __future__ import annotations

import pyqtgraph as pg


def retire_plot_item_after_shutdown(plot: pg.PlotItem) -> None:
    """Close normally, or resume a partial close without claiming false success."""
    view_box = plot.vb
    if plot.ctrlMenu is not None:
        # The tested pyqtgraph generations leave data items in ViewBox on
        # PlotItem.close(). Remove them while the scene and their Python
        # wrappers are still alive: 0.13.7 otherwise calls itemsChanged()
        # during Qt module shutdown after a PlotDataItem was deleted.
        plot.clear()
        if view_box is not None:
            _drain_view_box(view_box)
        _reconcile_terminal_lists(plot)
        plot.close()
    if plot.ctrlMenu is None:
        _finish_partial_close(plot)
    if (any(getattr(plot, field) is not None for field in ("ctrlMenu", "autoBtn", "axes", "vb"))
            or plot.items or plot.dataItems or plot.curves
            or view_box is not None and _remaining_view_items(view_box)):
        raise RuntimeError("pyqtgraph PlotItem terminal close is incomplete")


def _remaining_view_items(view_box: pg.ViewBox) -> tuple[object, ...]:
    # PlotItem.removeItem() pops its own list before ViewBox.removeItem();
    # ViewBox removes addedItems before detaching the QGraphics child. Both
    # collections are required to find a half-removed item on explicit retry.
    return tuple({id(item): item for item in (
        *view_box.addedItems, *view_box.childGroup.childItems())}.values())


def _drain_view_box(view_box: pg.ViewBox) -> None:
    for item in _remaining_view_items(view_box):
        view_box.removeItem(item)
    if _remaining_view_items(view_box):
        raise RuntimeError("pyqtgraph ViewBox terminal child removal is incomplete")


def _reconcile_terminal_lists(plot: pg.PlotItem) -> None:
    if plot.items:
        raise RuntimeError("pyqtgraph PlotItem still owns data items")
    # PlotItem.removeItem() updates curves only AFTER ViewBox.removeItem(). A
    # mid-removal error can leave a physically detached curve in this list.
    # Terminal cleanup has no further paint/decimation work to schedule.
    plot.curves.clear()
    plot.dataItems.clear()
    plot.avgCurves = {}


def _finish_partial_close(plot: pg.PlotItem) -> None:
    button = plot.autoBtn
    if button is not None:
        button.setParent(None)
        plot.autoBtn = None

    axes = plot.axes
    if axes is not None:
        for slot in axes.values():
            axis = slot["item"]
            label = axis.label
            if label is not None:
                scene = label.scene()
                if scene is not None:
                    scene.removeItem(label)
                axis.label = None
            scene = axis.scene()
            if scene is not None:
                scene.removeItem(axis)
        plot.axes = None

    view_box = plot.vb
    if view_box is not None:
        _drain_view_box(view_box)
        _reconcile_terminal_lists(plot)
        scene = view_box.scene()
        if scene is not None:
            scene.removeItem(view_box)
        plot.vb = None
