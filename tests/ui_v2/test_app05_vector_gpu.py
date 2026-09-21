"""Vector primitive correctness; no speed or whole-renderer acceptance claim."""
from dataclasses import replace
import unittest

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QApplication

from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, image_target, compare_images
from scripts.app05_scientific_gpu import draw_scientific_gpu
from scripts.app05_scientific_layers import CoverageRect, ScientificLayer, ScientificLayers, detach_layers, paint_layers
from scripts.app05_vector_geometry import pattern_rows, stroke_polygons
from tests.ui_v2.test_app05_scientific_layers import ScientificGeometryTests


def line_layer(path, *, width=3., style=Qt.PenStyle.SolidLine, color=QColor("white")):
    pen = QPen(color)
    pen.setCosmetic(True)
    pen.setWidthF(width)
    pen.setStyle(style)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    return ScientificLayer("curve", 0, 0., (0., 0., 80., 64.), (1., 0., 0., 1., 0., 0.), 1., path=path, pen=pen)


class VectorGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stroke_finite_subpaths_caps_joins_dash_and_admission(self):
        path = QPainterPath(QPointF(8, 20))
        path.lineTo(24, 20)
        path.moveTo(40, 20)
        path.lineTo(68, 20)
        layer = line_layer(path)
        for cap in (Qt.PenCapStyle.FlatCap, Qt.PenCapStyle.SquareCap, Qt.PenCapStyle.RoundCap):
            for join in (Qt.PenJoinStyle.BevelJoin, Qt.PenJoinStyle.RoundJoin, Qt.PenJoinStyle.MiterJoin):
                pen = QPen(layer.pen)
                pen.setCapStyle(cap)
                pen.setJoinStyle(join)
                polygons, info = stroke_polygons(replace(layer, pen=pen), SceneExtent(80, 64))
                self.assertGreater(info["vertices"], 0)
                self.assertLess(info["winding_edge_bound"], 254)
                combined = QPainterPath()
                combined.setFillRule(Qt.FillRule.WindingFill)
                for polygon in polygons:
                    combined.addPolygon(polygon)
                self.assertTrue(combined.contains(QPointF(15, 20)))
                self.assertFalse(combined.contains(QPointF(32, 20)), "gap must not become a connector")
        dashed = line_layer(path, style=Qt.PenStyle.DashLine)
        polygons, _ = stroke_polygons(dashed, SceneExtent(80, 64))
        self.assertGreater(len(polygons), 2)
        with self.assertRaises(MemoryError):
            stroke_polygons(layer, SceneExtent(80, 64), budget_bytes=32)
        remote = QPainterPath(QPointF(0, 0))
        remote.lineTo(1e20, 0)
        with self.assertRaisesRegex(ValueError, "unbounded"):
            stroke_polygons(line_layer(remote), SceneExtent(80, 64))

    def test_high_winding_is_rejected_not_wrapped_into_false_gaps(self):
        path = QPainterPath()
        for _ in range(130):
            path.moveTo(8, 20)
            path.lineTo(70, 20)
        with self.assertRaisesRegex(ValueError, "overflow"):
            stroke_polygons(line_layer(path), SceneExtent(80, 64))

    def test_detached_coverage_commands_match_actual_strip_including_mixed_flags(self):
        _, item, _, scene, waterfall, panels = ScientificGeometryTests.fixture(self)
        from sdr_monitor.ui.v2.spectrum.sweep_coverage_overlay import _CoverageStrip
        strip = _CoverageStrip()
        strip.setZValue(-5)
        item.getViewBox().addItem(strip)
        strip.set_rect(QRectF(100e6, -100, 4e6, 100))
        strip.runs = [(100e6+i*1e6, 101e6+i*1e6, state) for i, state in enumerate((1, 2, 4, 5))]
        scene.sweep_coverage.strip = strip
        reference = []
        class Recorder:
            def save(self): pass
            def restore(self): pass
            def setClipRect(self, rect): pass
            def setPen(self, pen): pass
            def deviceTransform(self): return QTransform()
            def resetTransform(self): pass
            def setWorldTransform(self, transform): pass
            def setRenderHint(self, *args): pass
            def setBrushOrigin(self, point): pass
            def setBrush(self, brush): self.brush = brush
            def drawRect(self, rect):
                reference.append((rect.getRect(), self.brush.color().getRgb(), self.brush.style().value))
        strip.paint(Recorder())
        bundle = detach_layers(scene, waterfall, panels)
        layer = next(value for value in bundle.layers if value.name == "coverage")
        from sdr_monitor.ui.v2.spectrum.coverage_pattern import coverage_pixel_rect
        self.assertEqual([(coverage_pixel_rect(QTransform(), QRectF(*r.rect)).getRect(), r.rgba, r.style)
                          for r in layer.coverage], reference)
        self.assertEqual([r.state for r in layer.coverage], [1, 2, 4, 4, 5, 5])
        self.assertEqual(layer.z, -5)
        strip.runs.clear()
        self.assertEqual(len(layer.coverage), 6, "descriptor must not retain mutable runs")


class VectorNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.target = SceneGpuTarget()
        self.addCleanup(self.target.close)
        if not self.target.available:
            self.skipTest("requires native Windows GL context")

    def render(self, layers, dpr=1.):
        extent = SceneExtent(80, 64, dpr)
        bundle = ScientificLayers(tuple(layers), 4096, 64*1024*1024)
        metric = {}
        def draw(device, functions, size):
            metric.update(draw_scientific_gpu(device, functions, size, bundle))
        image, _ = self.target.render(extent, [], QColor("black"), draw=draw)
        return image, metric

    def test_coverage_pattern_matches_cpu_under_dpr_translation_and_inverted_axes(self):
        for dpr in (1., 1.25, 1.5, 2.):
            for inverted in (False, True):
                for style in (Qt.BrushStyle.SolidPattern, Qt.BrushStyle.HorPattern,
                              Qt.BrushStyle.BDiagPattern, Qt.BrushStyle.DiagCrossPattern):
                    with self.subTest(dpr=dpr, inverted=inverted, style=style):
                        matrix = (1.3, 0., 0., -1.1 if inverted else 1.1, 9.3, 49.2 if inverted else 7.4)
                        rect = CoverageRect((0., 0., 40., 30.), (255, 0, 0, 255), style.value, 4)
                        layer = ScientificLayer('coverage', 0, -5, (0., 0., 80., 64.), matrix, 1.,
                                                coverage=(rect,), pattern_rect=rect.rect)
                        gpu, _ = self.render([layer], dpr)
                        cpu = image_target(SceneExtent(80, 64, dpr), QColor('black'))
                        paint_layers(cpu, ScientificLayers((layer,), 128, 1024*1024))
                        self.assertTrue(compare_images(cpu, gpu)['equal'], compare_images(cpu, gpu))

    def test_native_stroke_width_gaps_caps_dash_and_single_alpha_blend(self):
        path = QPainterPath(QPointF(8, 20))
        path.lineTo(24, 20)
        path.moveTo(40, 20)
        path.lineTo(68, 20)
        for dpr in (1., 1.5, 2.):
            image, metric = self.render([line_layer(path)], dpr)
            self.assertEqual(image.pixelColor(int(16*dpr), int(20*dpr)), QColor("white"))
            self.assertEqual(image.pixelColor(int(32*dpr), int(20*dpr)), QColor("black"))
            self.assertEqual(image.pixelColor(int(16*dpr), int(20*dpr)+3), QColor("black"))
            self.assertEqual(len(metric["vectors"]["strokes"]), 1)
        dashed, _ = self.render([line_layer(path, width=2, style=Qt.PenStyle.DashLine)])
        self.assertEqual(dashed.pixelColor(10, 20), QColor("white"))
        self.assertEqual(dashed.pixelColor(18, 20), QColor("black"))
        overlapped = QPainterPath(path)
        overlapped.addPath(path)
        alpha, _ = self.render([line_layer(overlapped, color=QColor(255, 255, 255, 128))])
        self.assertEqual(alpha.pixelColor(16, 20), QColor(128, 128, 128), "overlap must not blend the same stroke twice")

    def test_native_coverage_masks_clip_opacity_and_curve_z_order(self):
        extent = SceneExtent(80, 64)
        for style in (Qt.BrushStyle.SolidPattern, Qt.BrushStyle.HorPattern,
                      Qt.BrushStyle.BDiagPattern, Qt.BrushStyle.DiagCrossPattern):
            rect = CoverageRect((4., 4., 64., 40.), (255, 0, 0, 255), style.value, 4)
            layer = ScientificLayer("coverage", 0, -5., (8., 8., 56., 32.),
                (1., 0., 0., 1., 0., 0.), 1., coverage=(rect,))
            image, metric = self.render([layer])
            mask = pattern_rows(style.value)
            for y in range(8, 24):
                for x in range(8, 24):
                    expected = QColor("red" if mask[(y-8)%8] & (1 << ((x-8)%8)) else "black")
                    self.assertEqual(image.pixelColor(x, y), expected)
            self.assertEqual(image.pixelColor(7, 12), QColor("black"))
            self.assertEqual(metric["vectors"]["coverage_rectangles"], 1)
        path = QPainterPath(QPointF(10, 20))
        path.lineTo(50, 20)
        layer = replace(layer, coverage=(replace(rect, style=Qt.BrushStyle.SolidPattern.value, rgba=(255, 0, 0, 128)),))
        image, _ = self.render([layer, line_layer(path)])
        self.assertEqual(image.pixelColor(20, 20), QColor("white"))
        self.assertEqual(image.pixelColor(20, 30), QColor(128, 0, 0))
        self.assertLess(extent.nominal_target_bytes, extent.target_budget_bytes)

    def test_native_square_round_flat_caps_and_bevel_round_miter_corners(self):
        line = QPainterPath(QPointF(10, 20))
        line.lineTo(60, 20)
        for cap in (Qt.PenCapStyle.FlatCap, Qt.PenCapStyle.SquareCap, Qt.PenCapStyle.RoundCap):
            layer = line_layer(line, width=8)
            layer.pen.setCapStyle(cap)
            image, _ = self.render([layer])
            self.assertEqual(image.pixelColor(8, 20), QColor("black" if cap == Qt.PenCapStyle.FlatCap else "white"))
            self.assertEqual(image.pixelColor(6, 16), QColor("white" if cap == Qt.PenCapStyle.SquareCap else "black"))
        corner = QPainterPath(QPointF(10, 40))
        corner.lineTo(30, 40)
        corner.lineTo(30, 10)
        for join in (Qt.PenJoinStyle.BevelJoin, Qt.PenJoinStyle.RoundJoin, Qt.PenJoinStyle.MiterJoin):
            layer = line_layer(corner, width=8)
            layer.pen.setJoinStyle(join)
            image, _ = self.render([layer])
            self.assertEqual(image.pixelColor(33, 43), QColor("white" if join == Qt.PenJoinStyle.MiterJoin else "black"))
            self.assertEqual(image.pixelColor(32, 42), QColor("black" if join == Qt.PenJoinStyle.BevelJoin else "white"))


if __name__ == "__main__":
    unittest.main()
