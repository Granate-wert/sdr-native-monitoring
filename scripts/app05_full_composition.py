"""Bounded synchronous plot composition using actual Qt stacking/coordinates.

No retained plan/cache, event pumping, visibility mutation or source owner.
First verify CPU traversal against QGraphicsScene.render; then substitute only
identified scientific paint items. Shell/toolbars are outside this plot target.
"""
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QTransform
from PySide6.QtWidgets import QGraphicsItem, QStyle, QStyleOptionGraphicsItem


@dataclass(frozen=True)
class PlotCommand:
    item: object
    panel: int
    target: QRectF
    graphics: object
    scientific: object = None
    role: str = "qt"


def build_plot_plan(scene, waterfall, panels, bundle):
    by_name = {layer.name: layer for layer in bundle.layers}
    if len(by_name) != len(bundle.layers):
        raise ValueError("duplicate scientific layer names")
    replacements = {id(scene._persistence.image_item): by_name.get("persistence")}
    replacements.update({id(item.curve): by_name.get(kind.value) for kind, item in scene._curves.items()})
    replacements[id(scene.sweep_coverage.history.curve)] = by_name.get("previous-sweep")
    replacements[id(scene.sweep_coverage.strip)] = by_name.get("coverage")
    replacements.update({id(item): by_name.get(f"waterfall-{i}") for i, item in enumerate(waterfall.image_items)})
    roles = {id(item): f"marker:{name}:line" for name, item in scene._marker_lines.items()}
    roles.update({id(item): f"marker:{name}:label" for name, item in scene._marker_labels.items()})
    roles.update({id(item): f"band-mask:{index}" for index, item in enumerate(scene._band_mask_items)})
    roles[id(scene.sweep_coverage.label)] = "coverage-label"
    for panel, owner in enumerate((scene, waterfall)):
        for name in ("left", "bottom", "right", "top"):
            roles[id(owner._plot_item.getAxis(name))] = f"axis:{panel}:{name}"
    commands = []
    seen = []
    for index, (graphics, target) in enumerate(panels):
        items = graphics.scene().items(Qt.SortOrder.AscendingOrder)
        if len(items) > 512:
            raise ValueError("plot command bound exceeded")
        for item in items:
            if not item.isVisible() or item.effectiveOpacity() == 0:
                continue
            if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemHasNoContents:
                continue
            if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations:
                raise ValueError("transform-ignoring item requires explicit device mapping")
            if item.graphicsEffect() is not None:
                raise ValueError("graphics effect cannot be silently omitted")
            layer = replacements.get(id(item))
            if layer is not None:
                seen.append(layer.name)
            ancestor = item
            role = "qt"
            while ancestor is not None:
                if id(ancestor) in roles:
                    role = roles[id(ancestor)]
                    break
                ancestor = ancestor.parentItem()
            commands.append(PlotCommand(item, index, QRectF(target), graphics, layer, role))
    if sorted(seen) != sorted(by_name):
        raise AssertionError(("scientific substitution must be one-to-one", seen, list(by_name)))
    return tuple(commands)


def _state(command):
    item = command.item
    graphics, target = command.graphics, command.target
    source = graphics.mapToScene(graphics.viewport().rect()).boundingRect()
    panel = QTransform()
    panel.translate(target.x(), target.y())
    panel.scale(target.width()/source.width(), target.height()/source.height())
    panel.translate(-source.x(), -source.y())
    matrix = item.sceneTransform() * panel
    clip = QPainterPath()
    clip.addRect(target)
    ancestor = item
    own = True
    while ancestor is not None:
        flag = (QGraphicsItem.GraphicsItemFlag.ItemClipsToShape if own else
                QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        if ancestor.flags() & flag:
            mapped = (ancestor.sceneTransform() * panel).map(ancestor.shape())
            clip = clip.intersected(mapped)
        ancestor = ancestor.parentItem()
        own = False
    return matrix, clip


def paint_qt_commands(device, commands):
    painter = QPainter(device)
    if not painter.isActive():
        raise RuntimeError("Qt composition painter did not begin")
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for command in commands:
            item = command.item
            matrix, clip = _state(command)
            inverse, valid = matrix.inverted()
            if not valid:
                raise ValueError("singular Qt item transform")
            option = QStyleOptionGraphicsItem()
            option.exposedRect = inverse.mapRect(clip.boundingRect()).intersected(item.boundingRect())
            option.rect = item.boundingRect().toAlignedRect()
            option.state = QStyle.StateFlag.State_None
            if item.isEnabled():
                option.state |= QStyle.StateFlag.State_Enabled
            if item.isSelected():
                option.state |= QStyle.StateFlag.State_Selected
            if item.hasFocus():
                option.state |= QStyle.StateFlag.State_HasFocus
            painter.save()
            try:
                painter.setClipPath(clip)
                painter.setOpacity(item.effectiveOpacity())
                painter.setWorldTransform(matrix)
                item.paint(painter, option, command.graphics.viewport())
            finally:
                painter.restore()
    finally:
        painter.end()


def paint_plot_backgrounds(device, panels):
    painter = QPainter(device)
    if not painter.isActive():
        raise RuntimeError("plot background painter did not begin")
    try:
        for graphics, target in panels:
            painter.fillRect(target, graphics.backgroundBrush())
    finally:
        painter.end()


def draw_plot_composition(device, functions, extent, plan, panels, bundle):
    from scripts.app05_scientific_gpu import draw_scientific_gpu
    from scripts.app05_scientific_layers import ScientificLayers
    paint_plot_backgrounds(device, panels)
    pending = []
    science = []
    qt_batches = 0
    for command in plan:
        if command.scientific is None:
            pending.append(command)
            continue
        if pending:
            paint_qt_commands(device, pending)
            pending.clear()
            qt_batches += 1
        one = ScientificLayers((command.scientific,), bundle.retained_bytes, bundle.allocation_limit)
        detail = draw_scientific_gpu(device, functions, extent, one)
        science.append(dict(name=command.scientific.name, detail=detail))
    if pending:
        paint_qt_commands(device, pending)
        qt_batches += 1
    return dict(all_plot_layers=True, all_layers=True, qt_batches=qt_batches, scientific=science,
                product_accepted=False, scope="two plot viewports, not shell/toolbars or interactive widget integration")


def plan_metadata(plan):
    commands = []
    for command in plan:
        matrix, clip = _state(command)
        item = command.item
        commands.append(dict(panel=command.panel, type=type(item).__name__, z=item.zValue(), role=command.role,
            scientific=command.scientific.name if command.scientific is not None else None,
            transform=[matrix.m11(), matrix.m12(), matrix.m21(), matrix.m22(), matrix.dx(), matrix.dy()],
            clip=clip.boundingRect().getRect(), bounds=item.boundingRect().getRect(),
            text=item.toPlainText() if hasattr(item, "toPlainText") else None))
    return dict(count=len(commands), commands=commands, retained_beyond_capture=False)
