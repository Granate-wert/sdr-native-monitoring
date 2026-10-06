"""Retry-safe terminal retirement of a pyqtgraph PlotItem.

pyqtgraph 0.14.0 clears ``ctrlMenu`` early in ``PlotItem.close()`` and then
uses its absence to skip subsequent calls. If an axis or ViewBox removal
raises, a normal retry therefore needs to finish the remaining Qt items.
This helper is only for an acknowledged shell shutdown, never Live Stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid


@dataclass(slots=True)
class PlotTerminalOwnership:
    """Exact graphics wrappers captured before PlotItem.close() clears them."""

    plot: pg.PlotItem
    view_box: Any
    axes: tuple[Any, ...]
    axis_labels: tuple[tuple[Any, Any | None], ...]
    title_label: Any | None
    layout_entries: tuple[tuple[int, int, Any], ...]
    structural_complete: bool = False

    @property
    def labels(self) -> tuple[Any, ...]:
        """Captured labels, retaining axis pairing in ``axis_labels``."""
        return tuple(label for _axis, label in self.axis_labels if label is not None)


def capture_plot_terminal_ownership(plot: pg.PlotItem) -> PlotTerminalOwnership:
    """Capture only this PlotItem's graphics before terminal structural close."""
    view_box = plot.vb
    axes = tuple(slot["item"] for slot in (plot.axes or {}).values())
    if view_box is None or not axes:
        raise RuntimeError("plot terminal ownership is incomplete before close")
    if getattr(view_box, "name", None) is not None:
        raise RuntimeError("named terminal ViewBox ownership is unsupported")
    layout = plot.layout
    layout_entries: list[tuple[int, int, Any]] = []
    for row in range(layout.rowCount()):
        for column in range(layout.columnCount()):
            item = layout.itemAt(row, column)
            if item is not None:
                if item.parentLayoutItem() is not layout:
                    raise RuntimeError("plot terminal layout ownership is foreign")
                layout_entries.append((row, column, item))
    axis_labels = tuple((axis, axis.label) for axis in axes)
    for axis in axes:
        if axis.parentLayoutItem() is not layout:
            raise RuntimeError("plot terminal axis ownership is foreign")
        if axis.label is not None and axis.label.parentItem() is not axis:
            raise RuntimeError("plot terminal axis label ownership is foreign")
    title_label = plot.titleLabel
    if title_label is not None and title_label.parentLayoutItem() is not layout:
        raise RuntimeError("plot terminal title ownership is foreign")
    return PlotTerminalOwnership(
        plot=plot, view_box=view_box, axes=axes, axis_labels=axis_labels,
        title_label=title_label, layout_entries=tuple(layout_entries),
    )


def retire_plot_item_after_shutdown(
    plot: pg.PlotItem, *, ownership: PlotTerminalOwnership | None = None,
) -> None:
    """Close normally, or resume a partial close without claiming false success."""
    ownership = ownership or capture_plot_terminal_ownership(plot)
    if ownership.plot is not plot:
        raise RuntimeError("plot terminal ownership belongs to another PlotItem")
    if not ownership.structural_complete:
        _validate_captured_ownership(ownership)
    view_box = ownership.view_box
    if not ownership.structural_complete:
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
            _finish_partial_close(plot, ownership)
        _detach_owned_layout_entries(ownership)
        if (any(getattr(plot, field) is not None for field in ("ctrlMenu", "autoBtn", "axes", "vb"))
                or plot.items or plot.dataItems or plot.curves
                or view_box is not None and _remaining_view_items(view_box)):
            raise RuntimeError("pyqtgraph PlotItem terminal close is incomplete")
        ownership.structural_complete = True
    _retire_owned_wrappers(ownership)


def _detach_owned_layout_entries(ownership: PlotTerminalOwnership) -> None:
    layout = ownership.plot.layout
    orphan_ids = {id(item) for item in (*ownership.axes, ownership.view_box)}
    for _row, _column, captured in ownership.layout_entries:
        if id(captured) not in orphan_ids:
            continue
        match: Any | None = None
        for row in range(layout.rowCount()):
            for column in range(layout.columnCount()):
                item = layout.itemAt(row, column)
                if item is captured:
                    match = item
                    break
            if match is not None:
                break
        if match is None:
            if isValid(captured) and captured.parentLayoutItem() is not None:
                raise RuntimeError("owned plot layout entry moved under foreign ownership")
            continue
        if match.parentLayoutItem() is not layout:
            raise RuntimeError("owned plot layout entry has foreign parent")
        layout.removeItem(match)


def _validate_captured_ownership(ownership: PlotTerminalOwnership) -> None:
    """Reject wrappers adopted by another scene/layout before any retry work."""
    layout = ownership.plot.layout
    expected_scene = ownership.plot.scene()
    if getattr(ownership.view_box, "name", None) is not None:
        raise RuntimeError("named terminal ViewBox ownership is unsupported")
    _validate_captured_wrapper(ownership.view_box, expected_scene, layout, ownership.plot)
    for axis, captured_label in ownership.axis_labels:
        _validate_captured_wrapper(axis, expected_scene, layout, ownership.plot)
        if isValid(axis):
            current_label = axis.label
            if current_label is not None and current_label is not captured_label:
                raise RuntimeError("owned axis has a foreign current label")
        if captured_label is not None:
            _validate_captured_wrapper(captured_label, expected_scene, None, axis)
    if ownership.title_label is not None:
        _validate_captured_wrapper(ownership.title_label, expected_scene, layout, ownership.plot)


def _validate_captured_wrapper(
    wrapper: Any, expected_scene: object, expected_layout: object | None,
    expected_parent: object | None,
) -> None:
    if not isValid(wrapper):
        return
    scene = getattr(wrapper, "scene", None)
    if callable(scene) and (actual_scene := scene()) is not None and actual_scene is not expected_scene:
        raise RuntimeError("owned terminal wrapper was adopted by a foreign scene")
    parent_layout = getattr(wrapper, "parentLayoutItem", None)
    if callable(parent_layout) and (actual_layout := parent_layout()) is not None and actual_layout is not expected_layout:
        raise RuntimeError("owned terminal wrapper was adopted by a foreign layout")
    parent_item = getattr(wrapper, "parentItem", None)
    if callable(parent_item) and (actual_parent := parent_item()) is not None and actual_parent is not expected_parent:
        raise RuntimeError("owned terminal wrapper was adopted by a foreign graphics parent")


def _unregister_view_box(view_box: Any) -> None:
    if not isValid(view_box):
        return
    name = getattr(view_box, "name", None)
    if name is not None:
        if pg.ViewBox.NamedViews.get(name) is not view_box:
            raise RuntimeError("owned ViewBox has foreign named-view ownership")
        if view_box not in pg.ViewBox.AllViews:
            raise RuntimeError("named ViewBox registry ownership is incomplete")
    if view_box in pg.ViewBox.AllViews:
        # PlotItem.clear() and structural close already drained graphics;
        # unregister without re-entering ViewBox.clear() on a retry.
        view_box.unregister()


def _validate_detached_wrapper(wrapper: Any) -> None:
    if not isValid(wrapper):
        return
    scene = getattr(wrapper, "scene", None)
    if callable(scene) and scene() is not None:
        raise RuntimeError("owned terminal wrapper remains in a graphics scene")
    parent_item = getattr(wrapper, "parentItem", None)
    if callable(parent_item) and parent_item() is not None:
        raise RuntimeError("owned terminal wrapper retains a graphics parent")
    parent_layout = getattr(wrapper, "parentLayoutItem", None)
    if callable(parent_layout) and parent_layout() is not None:
        raise RuntimeError("owned terminal wrapper retains a layout parent")


def _delete_wrapper(wrapper: Any) -> None:
    if not isValid(wrapper):
        return
    _validate_detached_wrapper(wrapper)
    wrapper.deleteLater()
    QCoreApplication.sendPostedEvents(wrapper, QEvent.Type.DeferredDelete)
    if isValid(wrapper):
        raise RuntimeError("plot terminal wrapper deletion is incomplete")


def _retire_owned_wrappers(ownership: PlotTerminalOwnership) -> None:
    targets = (*ownership.labels, *ownership.axes, ownership.view_box)
    for wrapper in targets:
        _validate_detached_wrapper(wrapper)
    _unregister_view_box(ownership.view_box)
    # Delete the ViewBox first while its emptied child collections are still
    # directly observable; detached labels/axes then retire independently.
    _delete_wrapper(ownership.view_box)
    for wrapper in (*ownership.labels, *ownership.axes):
        _delete_wrapper(wrapper)


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


def _finish_partial_close(plot: pg.PlotItem, ownership: PlotTerminalOwnership) -> None:
    button = plot.autoBtn
    if button is not None:
        button.setParent(None)
        plot.autoBtn = None

    axes = ownership.axis_labels
    if axes:
        for axis, label in axes:
            if label is not None:
                if isValid(label):
                    scene = label.scene()
                    if scene is not None:
                        scene.removeItem(label)
                    if label.parentItem() is not None:
                        label.setParentItem(None)
                if isValid(axis) and axis.label is label:
                    axis.label = None
            if isValid(axis):
                scene = axis.scene()
                if scene is not None:
                    scene.removeItem(axis)
                if axis.parentItem() is not None:
                    axis.setParentItem(None)
        plot.axes = None

    view_box = ownership.view_box
    if view_box is not None:
        _drain_view_box(view_box)
        _reconcile_terminal_lists(plot)
        scene = view_box.scene()
        if scene is not None:
            scene.removeItem(view_box)
        if isValid(view_box) and view_box.parentItem() is not None:
            view_box.setParentItem(None)
        plot.vb = None
