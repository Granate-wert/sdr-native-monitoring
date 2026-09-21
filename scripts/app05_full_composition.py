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


def curve_support_comparison(reference, candidate, background):
    """Measurement-geometry witness, NOT an alternative pixel acceptance gate.

    Row-bounded one-device-pixel neighbourhood and per-column extrema detect
    missing peaks/columns beyond local aliased edge conventions. No resampling.
    """
    import sys
    import numpy as np
    from PySide6.QtGui import QImage
    expected = QImage.Format.Format_ARGB32_Premultiplied
    if (reference.size() != candidate.size() or reference.format() != expected or
            candidate.format() != expected or background.alpha() != 255):
        raise ValueError("curve support requires same-size ARGB32 images and opaque background")
    width, height = reference.width(), reference.height()
    bg = np.frombuffer(int(background.rgba()).to_bytes(4, sys.byteorder), dtype=np.uint8)
    arrays = [np.frombuffer(im.constBits(), dtype=np.uint8).reshape(height, im.bytesPerLine())
              for im in (reference, candidate)]
    low = [np.full(width, height, dtype=np.int32) for _ in arrays]
    high = [np.full(width, -1, dtype=np.int32) for _ in arrays]
    counts = [0, 0]
    unpaired = [0, 0]
    def support(index, y):
        return np.any(arrays[index][y, :width*4].reshape(width, 4) != bg, axis=1)
    for y in range(height):
        for index in (0, 1):
            occupied = support(index, y)
            counts[index] += int(np.count_nonzero(occupied))
            low[index][occupied] = np.minimum(low[index][occupied], y)
            high[index][occupied] = y
            nearby = np.zeros(width, dtype=bool)
            for other_y in range(max(0, y-1), min(height, y+2)):
                row = support(1-index, other_y)
                nearby |= row
                nearby[1:] |= row[:-1]
                nearby[:-1] |= row[1:]
            unpaired[index] += int(np.count_nonzero(occupied & ~nearby))
    shared = (high[0] >= 0) & (high[1] >= 0)
    def bounds(index):
        columns = np.flatnonzero(high[index] >= 0)
        return ([int(columns[0]), int(low[index][columns].min()), int(columns[-1]),
                 int(high[index][columns].max())] if columns.size else None)
    return dict(reference_pixels=counts[0], candidate_pixels=counts[1],
        reference_bounds=bounds(0), candidate_bounds=bounds(1),
        reference_pixels_without_candidate_within_one=unpaired[0],
        candidate_pixels_without_reference_within_one=unpaired[1],
        reference_only_columns=int(np.count_nonzero((high[0] >= 0) & (high[1] < 0))),
        candidate_only_columns=int(np.count_nonzero((high[1] >= 0) & (high[0] < 0))),
        shared_columns=int(np.count_nonzero(shared)),
        maximum_top_delta=int(np.max(np.abs(low[0][shared]-low[1][shared]), initial=0)),
        maximum_bottom_delta=int(np.max(np.abs(high[0][shared]-high[1][shared]), initial=0)),
        scope="isolated curve foreground; one physical pixel neighbourhood is diagnostic, NOT acceptance tolerance")


def review_plot_commands(target, extent, plan, background, bundle):
    """Isolate actual paint commands, not additive attribution of final pixels.

    Same actual transforms/clips; at most one CPU image and one GPU readback.
    No event pumping or mutated scene. Diagnostic only, never a speed sample.
    """
    from scripts.app05_scene_gpu_support import compare_images, image_target
    from scripts.app05_scientific_gpu import draw_scientific_gpu
    from scripts.app05_scientific_layers import ScientificLayers
    if len(plan) > 1024:
        raise ValueError("plot review command bound exceeded")
    row_scratch_bytes = extent.pixel_width * 128
    if extent.nominal_target_bytes + bundle.retained_bytes + row_scratch_bytes > extent.target_budget_bytes:
        raise MemoryError("plot review target/payload budget exceeded")
    rows = []
    for index, command in enumerate(plan):
        reference = image_target(extent, background)
        paint_qt_commands(reference, (command,))
        def draw(device, functions, size):
            if command.scientific is None:
                paint_qt_commands(device, (command,))
            else:
                one = ScientificLayers((command.scientific,), bundle.retained_bytes, bundle.allocation_limit)
                draw_scientific_gpu(device, functions, size, one)
        candidate, _ = target.render(extent, [], background, draw=draw)
        row = dict(index=index, role=command.role, type=type(command.item).__name__,
            scientific=command.scientific.name if command.scientific is not None else None,
            comparison=compare_images(reference, candidate))
        if command.scientific is not None and command.scientific.path is not None:
            row["curve_support"] = curve_support_comparison(reference, candidate, background)
        rows.append(row)
        del reference, candidate
    return dict(commands=rows, scope="isolated actual commands on same opaque background; NOT additive final-pixel attribution",
                nominal_row_scratch_bytes=row_scratch_bytes,
                speed_acceptance=False, product_accepted=False)
