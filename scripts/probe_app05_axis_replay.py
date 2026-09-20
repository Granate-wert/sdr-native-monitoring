"""Capture ONE real V2 cached axis and replay its actual QPicture/state.

Synthetic offscreen diagnostic only, NOT normal performance acceptance. Unlike
the scalar stage observer this intentionally owns one detached picture and paint
state, never a widget or source payload. Three extra same-device replays change
that diagnostic frame's alpha; ALL runner timing/visual results are contaminated.
No product code, hardware, cadence, worker or queue is changed.
"""
import hashlib
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
import weakref


def painter_state(painter):
    """Copy public paint state before QPicture.play can alter it."""
    from PySide6.QtGui import QBrush, QFont, QPainterPath, QPen, QRegion, QTransform
    device = painter.device()
    engine = painter.paintEngine()
    # QWidget reports logical dimensions; QImage/QPixmap report pixel storage.
    pixel_scale = device.devicePixelRatioF() if device.devType() == 1 else 1.0
    return dict(transform=QTransform(painter.worldTransform()),
        combined=QTransform(painter.combinedTransform()),
        device_transform=QTransform(painter.deviceTransform()),
        window=painter.window(), viewport=painter.viewport(),
        view_enabled=painter.viewTransformEnabled(), world_enabled=painter.worldMatrixEnabled(),
        clip=QPainterPath(painter.clipPath()), clipping=painter.hasClipping(),
        system_clip=QRegion(engine.systemClip()), hints=painter.renderHints(),
        opacity=painter.opacity(), composition=painter.compositionMode(),
        font=QFont(painter.font()), pen=QPen(painter.pen()), brush=QBrush(painter.brush()),
        brush_origin=painter.brushOrigin(), background=QBrush(painter.background()),
        background_mode=painter.backgroundMode(), layout=painter.layoutDirection(),
        width=device.width(), height=device.height(), dpr=device.devicePixelRatioF(),
        pixel_width=round(device.width() * pixel_scale), pixel_height=round(device.height() * pixel_scale),
        logical_dpi=[device.logicalDpiX(), device.logicalDpiY()],
        physical_dpi=[device.physicalDpiX(), device.physicalDpiY()],
        depth=device.depth(), device_type=device.devType(), engine_type=engine.type().name)


def restore_state(painter, state):
    from PySide6.QtCore import Qt
    painter.setWindow(state["window"])
    painter.setViewport(state["viewport"])
    painter.setViewTransformEnabled(state["view_enabled"])
    painter.setWorldTransform(state["transform"])
    painter.setWorldMatrixEnabled(state["world_enabled"])
    painter.setRenderHints(painter.renderHints(), False)
    painter.setRenderHints(state["hints"], True)
    painter.setOpacity(state["opacity"])
    painter.setCompositionMode(state["composition"])
    painter.setFont(state["font"])
    painter.setPen(state["pen"])
    painter.setBrush(state["brush"])
    painter.setBrushOrigin(state["brush_origin"])
    painter.setBackground(state["background"])
    painter.setBackgroundMode(state["background_mode"])
    painter.setLayoutDirection(state["layout"])
    # Implicit engine clip belongs to begin_replay BEFORE painter.begin().
    painter.setClipping(False)
    if state["clipping"]:
        painter.setClipPath(state["clip"], Qt.ClipOperation.ReplaceClip)


def matrix(value):
    return [getattr(value, name)() for name in
            ("m11", "m12", "m13", "m21", "m22", "m23", "m31", "m32", "m33")]


def describe(state):
    result = {key: state[key] for key in ("width", "height", "pixel_width", "pixel_height", "dpr", "logical_dpi", "physical_dpi",
        "depth", "device_type", "engine_type", "view_enabled", "world_enabled", "clipping", "opacity")}
    for name in ("transform", "combined", "device_transform"):
        result[name] = matrix(state[name])
    for name in ("window", "viewport"):
        result[name] = list(state[name].getRect())
    result.update(hints=state["hints"].value, composition=state["composition"].name,
        font=state["font"].toString(), clip_bounds=list(state["clip"].boundingRect().getRect()),
        clip_elements=state["clip"].elementCount(),
        system_clip_rects=[list(rect.getRect()) for rect in state["system_clip"]])
    return result


def image_for(state):
    from PySide6.QtGui import QImage
    if not 1 <= state["pixel_width"] <= 8192 or not 1 <= state["pixel_height"] <= 8192:
        raise ValueError("diagnostic target exceeds 8192 pixels per dimension")
    image = QImage(state["pixel_width"], state["pixel_height"],
                   QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise RuntimeError("could not allocate diagnostic image")
    image.setDevicePixelRatio(state["dpr"])
    image.setDotsPerMeterX(round(state["logical_dpi"][0] / .0254))
    image.setDotsPerMeterY(round(state["logical_dpi"][1] / .0254))
    image.fill(0xff202020)
    return image


def begin_replay(target, state, *, system_clip=True):
    """Install implicit device clip BEFORE begin; changing it mid-paint is stale.

    Use a short setup painter only to derive the exact target device transform,
    then begin the measured painter against the prepared engine. Setup is not
    part of picture replay timing. This also handles DPR/view transforms.
    """
    from PySide6.QtGui import QPainter, QRegion
    setup = QPainter(target)
    try:
        restore_state(setup, state)
        inverse, invertible = state["device_transform"].inverted()
        if not invertible:
            raise ValueError("non-invertible captured device transform")
        clip = (inverse * setup.deviceTransform()).map(state["system_clip"]) if system_clip else QRegion()
    finally:
        setup.end()
    target.paintEngine().setSystemClip(clip)
    painter = QPainter(target)
    restore_state(painter, state)
    return painter


def replay(picture, state, *, system_clip=True, repeats=40):
    image = image_for(state)
    painter = begin_replay(image, state, system_clip=system_clip)
    durations = []
    try:
        effective = describe(painter_state(painter))
        for index in range(repeats + 3):
            painter.save()
            begin = perf_counter()
            success = picture.play(painter)
            duration = (perf_counter() - begin) * 1000
            painter.restore()
            if not success:
                raise RuntimeError("QPicture replay failed")
            if index >= 3:
                durations.append(duration)
    finally:
        painter.end()
    return dict(p50_ms=median(durations), min_ms=min(durations), max_ms=max(durations),
                repeats=repeats, effective_state=effective)


def pixels(picture, state):
    """Single pass, unlike the deliberately overdrawn timing image."""
    image = image_for(state)
    painter = begin_replay(image, state)
    try:
        if not picture.play(painter):
            raise RuntimeError("pixel replay failed")
    finally:
        painter.end()
    return bytes(image.constBits())


def main():
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    root = Path(sys.argv[sys.argv.index("--checkout") + 1]).resolve(strict=True)
    output = Path(sys.argv[sys.argv.index("--output") + 1]).resolve()
    picture_path = output.with_suffix(".qpic")
    reference_path = output.with_suffix(".png")
    if output.exists() or picture_path.exists() or reference_path.exists():
        raise SystemExit("new output/picture paths required")
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QPicture
    from pyqtgraph import AxisItem
    from scripts import benchmark_app05_rtbw_observation as runner
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    target = None
    captured: dict[str, Any] = {}
    latest_specs: dict[str, Any] = {}
    timings: list[float] = []
    errors: list[str] = []
    original_init, original_paint = SpectrumScene.__init__, AxisItem.paint
    original_record = AxisItem.drawPicture

    def recorded(axis, painter, axis_spec, tick_specs, text_specs):
        value = original_record(axis, painter, axis_spec, tick_specs, text_specs)
        if target is not None and target() is axis and not captured:
            # All are small paint-command metadata, not scientific data arrays.
            # Deep Qt value copies so no mutable device/style objects survive.
            from PySide6.QtCore import QPointF, QRectF
            from PySide6.QtGui import QFont, QPen
            def line(spec):
                pen, first, last = spec
                return QPen(pen), QPointF(first), QPointF(last)
            if len(tick_specs) > 1024 or len(text_specs) > 1024:
                raise ValueError("axis command capture exceeds diagnostic bound")
            latest_specs.update(axis=line(axis_spec), ticks=[line(row) for row in tick_specs],
                texts=[(QRectF(rect), int(flags), str(text)) for rect, flags, text in text_specs],
                font=QFont(axis.style["tickFont"]) if axis.style["tickFont"] else None,
                pen=QPen(axis.textPen()), bounds=QRectF(axis.boundingRect()))
        return value

    def initialized(scene, *args, **kwargs):
        nonlocal target
        original_init(scene, *args, **kwargs)
        scene.set_persistence_render_mode(PersistenceRenderMode.DIRECT)
        target = weakref.ref(scene.plot_item.getAxis("left"))

    def painted(axis: Any, painter, option, widget):
        bound_axis = target() if target is not None else None
        selected = bool(axis is not None and bound_axis is axis and getattr(axis, "picture", None) is not None)
        capture_now = selected and len(timings) == 40 and not captured and not errors
        state = painter_state(painter) if capture_now else None
        begin = perf_counter()
        value = original_paint(axis, painter, option, widget)
        elapsed = (perf_counter() - begin) * 1000
        if selected and len(timings) < 512:
            timings.append(elapsed)
        if capture_now:
            # One detached QPicture, bounded lifecycle in this diagnostic only.
            try:
                picture = QPicture(axis.picture)
                if not picture.save(str(picture_path)):
                    raise RuntimeError("QPicture save failed")
                same_device = []
                for _ in range(3):
                    painter.save()
                    try:
                        restore_state(painter, state)
                        begin = perf_counter()
                        success = picture.play(painter)
                        same_device.append((perf_counter() - begin) * 1000)
                    finally:
                        painter.restore()
                    if not success:
                        raise RuntimeError("same-device replay failed")
                captured.update(picture=picture, state=state, original_ms=elapsed,
                    same_device_ms=same_device, bounds=list(axis.boundingRect().getRect()),
                    range=list(axis.range), geometry=list(axis.geometry().getRect()))
            except Exception as error:
                errors.append(repr(error))
        return value

    with patch.object(SpectrumScene, "__init__", initialized), patch.object(AxisItem, "paint", painted), \
         patch.object(AxisItem, "drawPicture", recorded):
        report, _, budget, _ = runner.main()
    if errors or not captured:
        raise RuntimeError((errors, "capture missing" if not captured else "capture failed"))
    picture, state = captured.pop("picture"), captured.pop("state")
    from PySide6.QtGui import QPainter
    axis_proxy = SimpleNamespace(style={"tickFont": latest_specs["font"]},
        textPen=lambda: latest_specs["pen"], boundingRect=lambda: latest_specs["bounds"])
    def direct(painter):
        original_record(axis_proxy, painter, latest_specs["axis"], latest_specs["ticks"], latest_specs["texts"])
        return True
    rebuilt = QPicture()
    painter = QPainter(rebuilt)
    try:
        if latest_specs["font"]:
            painter.setFont(latest_specs["font"])
        direct(painter)
    finally:
        painter.end()
    # A changed command stream would invalidate the direct-versus-picture test.
    from PySide6.QtCore import QBuffer, QIODevice
    def serialized(pic):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pic.save(buffer):
            raise RuntimeError("cannot serialize reconstructed axis")
        return buffer.data().data()
    if serialized(picture) != serialized(rebuilt):
        raise AssertionError("captured draw specs do not reconstruct the exact QPicture")
    direct_picture = SimpleNamespace(play=direct)
    exact_pixels = pixels(picture, state)
    if pixels(direct_picture, state) != exact_pixels:
        raise AssertionError("direct real-axis draw changed pixels")
    reloaded = QPicture()
    if not reloaded.load(str(picture_path)) or pixels(reloaded, state) != exact_pixels:
        raise AssertionError("saved/reloaded picture changed the reference pixels")
    from PySide6.QtGui import QImage
    reference = QImage(exact_pixels, state["pixel_width"], state["pixel_height"],
                       QImage.Format.Format_ARGB32_Premultiplied)
    if not reference.save(str(reference_path)):
        raise RuntimeError("could not save exact CPU reference")
    def component(kind):
        def draw(painter):
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            if kind == "text":
                if latest_specs["font"]:
                    painter.setFont(latest_specs["font"])
                painter.setPen(latest_specs["pen"])
                painter.setClipRect(latest_specs["bounds"].toAlignedRect())
                for rect, flags, text in latest_specs["texts"]:
                    painter.drawText(rect, flags, text)
            else:
                from PySide6.QtCore import QPointF
                for pen, first, last in [latest_specs["axis"], *latest_specs["ticks"]]:
                    if kind == "lines_noncosmetic":
                        from PySide6.QtGui import QPen
                        pen = QPen(pen)
                        pen.setCosmetic(False)
                    painter.setPen(pen)
                    if kind == "lines_reversed":
                        first, last = last, first
                    elif kind == "lines_integer":
                        first, last = QPointF(first.toPoint()), QPointF(last.toPoint())
                    painter.drawLine(first, last)
            return True
        return SimpleNamespace(play=draw)
    components = {kind: replay(component(kind), state) for kind in
                  ("lines", "text", "lines_reversed", "lines_integer", "lines_noncosmetic")}
    components["lines_reversed"]["pixels_equal_original_lines"] = (
        pixels(component("lines_reversed"), state) == pixels(component("lines"), state))
    components["lines_integer"]["pixels_equal_original_lines"] = (
        pixels(component("lines_integer"), state) == pixels(component("lines"), state))
    components["lines_noncosmetic"]["pixels_equal_original_lines"] = (
        pixels(component("lines_noncosmetic"), state) == pixels(component("lines"), state))
    def line_description(row):
        pen, first, last = row
        return dict(color=pen.color().getRgb(), width=pen.widthF(), cosmetic=pen.isCosmetic(),
            style=pen.style().name, dash=pen.dashPattern(),
            first=[first.x(), first.y()], last=[last.x(), last.y()])
    evidence = dict(scope=__doc__, capture=captured, state=describe(state),
        cached_axis_samples=len(timings), cached_axis_p50_ms=median(timings),
        qpicture_sha256=hashlib.sha256(picture_path.read_bytes()).hexdigest(),
        qpicture_bytes=picture_path.stat().st_size,
        replay_with_system_clip=replay(picture, state),
        replay_without_system_clip=replay(picture, state, system_clip=False),
        direct_actual_specs=replay(direct_picture, state), components=components,
        direct_pixels_bitidentical=True, serialized_pixels_bitidentical=True,
        pixel_sha256=hashlib.sha256(exact_pixels).hexdigest(),
        actual_axis=line_description(latest_specs["axis"]),
        actual_ticks=[line_description(row) for row in latest_specs["ticks"]],
        actual_labels=len(latest_specs["texts"]), reconstructed_picture_bitidentical=True,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        final_reserved_bytes=budget.snapshot().reserved_bytes)
    report["axis_replay_diagnostic"] = evidence
    report["normal_performance_acceptance"] = False
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(cached_p50_ms=evidence["cached_axis_p50_ms"],
        captured_ms=captured["original_ms"], same_device_ms=captured["same_device_ms"],
        image_p50_ms=evidence["replay_with_system_clip"]["p50_ms"],
        direct_p50_ms=evidence["direct_actual_specs"]["p50_ms"],
        components={name: {key: value for key, value in row.items() if key != "effective_state"}
                    for name, row in components.items()},
        picture_exact=True, pixels_exact=True, workers=report["remaining_workers"],
        outside=report["product_imports_outside_checkout"], reserved=evidence["final_reserved_bytes"]), indent=2))


if __name__ == "__main__":
    main()
