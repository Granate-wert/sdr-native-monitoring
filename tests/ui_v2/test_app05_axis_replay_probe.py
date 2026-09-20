"""The diagnostic must reproduce physical clip/transform, not a similar grid."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPicture, QRegion, QTransform
from PySide6.QtWidgets import QApplication

SPEC = importlib.util.spec_from_file_location("axis_probe",
    Path(__file__).resolve().parents[2] / "scripts/probe_app05_axis_replay.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class AxisReplayProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def picture(self):
        picture = QPicture()
        painter = QPainter(picture)
        try:
            painter.setPen(QPen(QColor(130, 190, 230, 70), 1.))
            for y in (2.25, 10.5, 21.75, 33.):
                painter.drawLine(QPointF(1.5, y), QPointF(65.25, y))
        finally:
            painter.end()
        return picture

    def test_exact_state_replay_at_four_scales_and_four_dprs(self):
        picture = self.picture()
        for dpr in (1., 1.25, 1.5, 2.):
            for scale in (1., 1.25, 1.5, 2.):
                with self.subTest(dpr=dpr, scale=scale):
                    image = QImage(180, 160, QImage.Format.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(dpr)
                    image.setDotsPerMeterX(3780)
                    image.setDotsPerMeterY(3780)
                    image.fill(0xff202020)
                    painter = QPainter(image)
                    try:
                        painter.translate(3.25, 5.5)
                        painter.scale(scale, scale)
                        painter.setOpacity(.7)
                        path = QPainterPath()
                        path.addRoundedRect(0., 0., 55., 40., 4., 4.)
                        painter.setClipPath(path)
                        state = PROBE.painter_state(painter)
                        self.assertTrue(picture.play(painter))
                    finally:
                        painter.end()
                    self.assertEqual(PROBE.pixels(picture, state), bytes(image.constBits()))
                    replay_image = PROBE.image_for(state)
                    self.assertEqual(replay_image.size(), image.size())
                    self.assertEqual(replay_image.devicePixelRatioF(), dpr)

    def test_backing_store_system_clip_is_translated_to_replay_device(self):
        image = QImage(200, 150, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        try:
            painter.translate(5, 5)
            state = PROBE.painter_state(painter)
            state["device_transform"] = QTransform.fromTranslate(78, 134)
            state["system_clip"] = QRegion(QRect(130, 167, 60, 50))
        finally:
            painter.end()
        replay = PROBE.begin_replay(image, state)
        try:
            self.assertEqual(replay.paintEngine().systemClip(), QRegion(QRect(57, 38, 60, 50)))
        finally:
            replay.end()

    def test_captured_qt_state_is_detached_from_later_painter_mutations(self):
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        try:
            painter.translate(3, 4)
            painter.setPen(QColor("red"))
            state = PROBE.painter_state(painter)
            painter.translate(50, 60)
            painter.setPen(QColor("blue"))
            self.assertEqual(state["transform"].dx(), 3)
            self.assertEqual(state["pen"].color(), QColor("red"))
        finally:
            painter.end()

    def test_replay_allocation_has_a_hard_size_bound(self):
        with self.assertRaisesRegex(ValueError, "8192"):
            PROBE.image_for(dict(pixel_width=8193, pixel_height=1))

    def test_system_clip_restricts_pixels_on_first_and_repeated_replay(self):
        source = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(source)
        state = PROBE.painter_state(painter)
        painter.end()
        state["system_clip"] = QRegion(QRect(20, 10, 10, 15))
        picture = self.picture()
        for _ in range(2):
            raw = PROBE.pixels(picture, state)
            result = QImage(raw, 100, 100, QImage.Format.Format_ARGB32_Premultiplied)
            self.assertEqual(result.pixelColor(5, 21), QColor(32, 32, 32))
            self.assertNotEqual(result.pixelColor(25, 21), QColor(32, 32, 32))


class ActualAxisCaptureTests(unittest.TestCase):
    def test_real_v2_capture_is_exact_bounded_and_not_a_normal_speed_gate(self):
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-actual-axis-") as directory:
            output = Path(directory) / "axis.json"
            run = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_axis_replay.py"),
                "--checkout", str(root), "--output", str(output), "--seconds", "4", "--cycles", "1",
                "--bins", "4096", "--source-hz", "200", "--driver-stop-ms", "100",
                "--persistence-power-bins", "32", "--persistence-every", "10"],
                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue(output.with_suffix(".qpic").is_file())
            self.assertTrue(output.with_suffix(".png").is_file())
            report = json.loads(output.read_text(encoding="utf-8"))
        capture = report["axis_replay_diagnostic"]
        self.assertFalse(report["normal_performance_acceptance"])
        self.assertTrue(capture["reconstructed_picture_bitidentical"])
        self.assertTrue(capture["direct_pixels_bitidentical"])
        self.assertTrue(capture["serialized_pixels_bitidentical"])
        self.assertGreaterEqual(capture["cached_axis_samples"], 41)
        self.assertLessEqual(capture["cached_axis_samples"], 512)
        self.assertEqual(len(capture["capture"]["same_device_ms"]), 3)
        self.assertGreater(capture["actual_labels"], 0)
        self.assertGreater(len(capture["actual_ticks"]), 0)
        # Never assert machine-dependent timings or altered-geometry parity.
        for name in ("witness_misses", "witness_evictions", "changed_during_paint"):
            self.assertEqual(report[name], 0)
        self.assertEqual(capture["final_reserved_bytes"], 0)
        self.assertEqual(report["remaining_workers"], [])
        self.assertEqual(report["product_imports_outside_checkout"], [])


if __name__ == "__main__":
    unittest.main()
