"""Isolated GPU feasibility for a TRUSTED actual-axis capture, never product UI.

One FBO, no SDR, visible window, worker, scientific queue or product cache.
CPU versus QPainter/OpenGL on the SAME picture. GPU timings include glFinish,
exclude readback; neither path measures Qt scene, composition, swap or DWM FPS.
Only captured DPR1/unclipped user state is supported; implicit system clip is
restored separately. Other states fail explicitly, never silently approximate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter


def validate_capture(report, picture_bytes):
    if not 1 <= len(picture_bytes) <= 1024 * 1024:
        raise ValueError("trusted capture picture must be 1..1048576 bytes")
    axis = report["axis_replay_diagnostic"]
    state = axis["state"]
    if hashlib.sha256(picture_bytes).hexdigest() != axis["qpicture_sha256"]:
        raise ValueError("picture checksum does not match capture")
    if state["clipping"] or state["view_enabled"] or state["dpr"] != 1.:
        raise ValueError("only DPR1, no user clip/view transform supported in this probe")
    if state["composition"] != "CompositionMode_SourceOver" or state["opacity"] != 1.:
        raise ValueError("unexpected captured composition/opacity")
    if not 1 <= state["width"] <= 4096 or not 1 <= state["height"] <= 2160:
        raise ValueError("capture exceeds bounded 4096x2160 GPU probe target")
    return state


def reference_gate(*, context_created, reference_matches, cpu_gpu_equal, gl_error):
    """Two equally wrong images must never pass a portability/quality gate."""
    return bool(context_created and reference_matches and cpu_gpu_equal and gl_error == 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=("offscreen", "windows"), default="windows")
    parser.add_argument("--smoke-only", action="store_true", help="Pixel/teardown checks only, no timing series")
    args = parser.parse_args()
    if not sys.flags.isolated or args.output.exists():
        parser.error("Python -I and a new output path required")
    picture_path = args.capture.with_suffix(".qpic")
    report = json.loads(args.capture.read_text(encoding="utf-8"))
    state = validate_capture(report, picture_path.read_bytes())
    os.environ["QT_QPA_PLATFORM"] = args.platform
    from PySide6.QtCore import QRect, QSize, Qt
    from PySide6.QtGui import QFont, QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext
    from PySide6.QtGui import QPainter, QPicture, QRegion, QTransform
    from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLPaintDevice
    # QPicture's replay scale depends on Qt's default DPI, which differs between
    # offscreen capture and Windows. Match the captured 96-DPI reference; do not
    # silently accept CPU/GPU agreement on a rescaled (wrong) picture.
    if state["logical_dpi"] != [96, 96]:
        raise ValueError("this first GPU probe requires the captured 96-DPI fixture")
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_Use96Dpi)
    app = QGuiApplication([])
    surface = QOffscreenSurface()
    surface.create()
    context = QOpenGLContext()
    created = context.create()
    current = created and context.makeCurrent(surface)
    evidence = dict(scope=__doc__, platform=args.platform, smoke_only=args.smoke_only, context_created=created,
        surface_valid=surface.isValid(), made_current=current, product_changes=False,
        capture_sha256=hashlib.sha256(args.capture.read_bytes()).hexdigest(),
        qpicture_sha256=hashlib.sha256(picture_path.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    fbo = device = None
    try:
        if current:
            functions = context.functions()
            evidence.update(renderer=functions.glGetString(0x1F01), vendor=functions.glGetString(0x1F00),
                gl_version=functions.glGetString(0x1F02), gles=context.isOpenGLES())
            picture = QPicture()
            if not picture.load(str(picture_path)):
                raise RuntimeError("cannot load trusted picture")
            size = QSize(state["width"], state["height"])
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
            fmt.setInternalTextureFormat(0x8058)  # GL_RGBA8; no MSAA
            fbo = QOpenGLFramebufferObject(size, fmt)
            if not fbo.isValid() or not fbo.bind():
                raise RuntimeError("GPU target creation/bind failed")
            device = QOpenGLPaintDevice(size)
            device.setDotsPerMeterX(round(state["logical_dpi"][0] / .0254))
            device.setDotsPerMeterY(round(state["logical_dpi"][1] / .0254))
            cpu = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            cpu.setDotsPerMeterX(round(state["logical_dpi"][0] / .0254))
            cpu.setDotsPerMeterY(round(state["logical_dpi"][1] / .0254))
            font = QFont()
            if not font.fromString(state["font"]):
                raise ValueError("could not restore capture font")
            transform = QTransform(*state["transform"])
            source_device = QTransform(*state["device_transform"])
            inverse, invertible = source_device.inverted()
            if not invertible:
                raise ValueError("captured device transform is singular")
            system_clip = QRegion()
            for rect in state["system_clip_rects"]:
                system_clip = system_clip.united(QRegion(QRect(*rect)))

            def draw(target):
                target.paintEngine().setSystemClip((inverse * transform).map(system_clip))
                painter = QPainter(target)
                if not painter.isActive():
                    raise RuntimeError("cannot create target painter")
                try:
                    painter.setWorldTransform(transform)
                    painter.setFont(font)
                    painter.setRenderHints(QPainter.RenderHint(state["hints"]))
                    painter.setClipping(False)
                    if not picture.play(painter):
                        raise RuntimeError("picture draw failed")
                finally:
                    painter.end()

            def run(kind, count):
                times = []
                for _ in range(count):
                    if kind == "gpu":
                        if fbo is None:
                            raise RuntimeError("GPU target already released")
                        if not fbo.bind():
                            raise RuntimeError("GPU target bind failed")
                        functions.glViewport(0, 0, size.width(), size.height())
                        functions.glDisable(0x0C11)  # scissor left by a previous painter
                        functions.glClearColor(32 / 255, 32 / 255, 32 / 255, 1.)
                        functions.glClear(0x4000 | 0x0100 | 0x0400)
                        functions.glFinish()
                        begin = perf_counter()
                        draw(device)
                        functions.glFinish()  # measure completed work, not command submission
                    else:
                        cpu.fill(0xff202020)
                        begin = perf_counter()
                        draw(cpu)
                    times.append((perf_counter() - begin) * 1000)
                return dict(p50_ms=median(times), min_ms=min(times), max_ms=max(times), samples=count)

            if not args.smoke_only:
                run("cpu", 3)
                run("gpu", 3)
                evidence["micro_abba"] = [dict(kind=kind, **run(kind, 40)) for kind in ("cpu", "gpu", "gpu", "cpu")]
            run("gpu", 1)
            gpu_image = fbo.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            run("cpu", 1)
            import numpy as np
            cpu_pixels = np.frombuffer(cpu.constBits(), dtype=np.uint8).reshape(size.height(), size.width(), 4)
            gpu_pixels = np.frombuffer(gpu_image.constBits(), dtype=np.uint8).reshape(size.height(), size.width(), 4)
            delta = np.abs(cpu_pixels.astype(np.int16) - gpu_pixels.astype(np.int16))
            reference = QImage(str(args.capture.with_suffix(".png"))).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            if reference.isNull() or reference.size() != size:
                raise ValueError("portable CPU reference PNG required")
            if hashlib.sha256(reference.constBits()).hexdigest() != report["axis_replay_diagnostic"]["pixel_sha256"]:
                raise ValueError("reference PNG checksum does not match capture")
            reference_pixels = np.frombuffer(reference.constBits(), dtype=np.uint8).reshape(size.height(), size.width(), 4)
            difference = np.any(reference_pixels != cpu_pixels, axis=2)
            ys, xs = np.where(difference)
            evidence["reference_difference"] = dict(pixels=int(len(xs)),
                bounds=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None,
                examples=[dict(x=int(x), y=int(y), reference=reference_pixels[y, x].tolist(), cpu=cpu_pixels[y, x].tolist())
                          for x, y in zip(xs[:8], ys[:8])])
            evidence.update(pixel_equal=bool(np.array_equal(cpu_pixels, gpu_pixels)),
                different_pixels=int(np.count_nonzero(np.any(delta, axis=2))),
                max_channel_error=int(delta.max()), total_pixels=size.width() * size.height(),
                cpu_pixels_sha256=hashlib.sha256(cpu.constBits()).hexdigest(),
                gpu_pixels_sha256=hashlib.sha256(gpu_image.constBits()).hexdigest(),
                capture_reference_matches=hashlib.sha256(cpu.constBits()).hexdigest() == report["axis_replay_diagnostic"]["pixel_sha256"],
                qpicture_logical_dpi=[picture.logicalDpiX(), picture.logicalDpiY()],
                target_logical_dpi=[device.logicalDpiX(), device.logicalDpiY()],
                actual_paint_engine=device.paintEngine().type().name,
                gl_error=functions.glGetError(),
                nominal_color_depth_stencil_bytes=size.width() * size.height() * 8,
                memory_scope="One RGBA8 color plus nominal D24S8; excludes driver, Qt and CPU reference/scratch allocations",
                interpretation="Micro feasibility ONLY. Pixel mismatch blocks drop-in acceptance; no application FPS gain inferred.")
            fbo.release()
    finally:
        # Delete GL resources while their owning context remains current.
        device = None
        fbo = None
        context.doneCurrent()
        surface.destroy()
        evidence["context_released"] = QOpenGLContext.currentContext() is None
        evidence["surface_destroyed"] = not surface.isValid()
    if app.platformName() != args.platform:
        raise AssertionError("unexpected Qt platform")
    evidence["micro_reference_gate_passed"] = reference_gate(context_created=current,
        reference_matches=evidence.get("capture_reference_matches", False),
        cpu_gpu_equal=evidence.get("pixel_equal", False), gl_error=evidence.get("gl_error"))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
