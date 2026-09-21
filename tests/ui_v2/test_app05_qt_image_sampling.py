"""Explicit optional Qt raster compatibility; default texel contract is unchanged."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from scripts.app05_scene_gpu_support import SceneExtent, SceneGpuTarget, image_target, compare_images
from scripts.app05_scientific_gpu import draw_scientific_gpu, qt_sampling_inverse, sampled_image_scissor
from scripts.app05_scientific_layers import ScientificLayer, ScientificLayers


class QtImageSamplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_qt_indexed_and_rgba_images_match_fractional_geometry_and_long_spans(self):
        target = SceneGpuTarget()
        self.addCleanup(target.close)
        if not target.available:
            self.skipTest('requires native GL')
        for fmt in (QImage.Format.Format_Indexed8, QImage.Format.Format_RGBA8888):
            source = QImage(3073, 7, fmt)
            raw = np.frombuffer(source.bits(), np.uint8).reshape(source.height(), source.bytesPerLine())
            raw[:] = np.random.default_rng(73).integers(0, 256, raw.shape, dtype=np.uint8)
            if fmt is QImage.Format.Format_Indexed8:
                source.setColorTable([QColor(i, 255-i, (i*13)%256, 255).rgba() for i in range(256)])
            else:
                raw[:, 3::4] = 255
                raw[:, 3::28] = 0  # Unknown/missing pixels, not a fabricated low level.
            pixels = source.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied).copy()
            for dpr in (1., 1.25, 1.5, 2.):
                for inverted in (False, True):
                    with self.subTest(format=fmt, dpr=dpr, inverted=inverted):
                        extent = SceneExtent(2200, 80, dpr)
                        sy = -6.25 if inverted else 6.25
                        layer = ScientificLayer('persistence', 0, 0, (7.25, 5.75, 2181.5, 63.5),
                            (.713, 0., 0., sy, 4.3, 64.5 if inverted else 8.5), 1., image=pixels,
                            local_rect=(0., 0., 3073., 7.), source_format=fmt.value, sampling='qt611-nearest')
                        bundle = ScientificLayers((layer,), pixels.sizeInBytes(), 64*1024*1024)
                        cpu = image_target(extent, QColor('black'))
                        painter = QPainter(cpu)
                        painter.setClipRect(QRectF(*layer.clip))
                        painter.setTransform(layer.matrix())
                        painter.drawImage(QRectF(*layer.local_rect), source)
                        painter.end()
                        gpu, _ = target.render(extent, [], QColor('black'),
                            draw=lambda device, functions, size: draw_scientific_gpu(device, functions, size, bundle))
                        comparison = compare_images(cpu, gpu)
                        self.assertTrue(comparison['equal'], comparison)

    def test_sampling_mode_is_explicit_and_unknown_modes_reject(self):
        source = QImage(2, 2, QImage.Format.Format_RGBA8888_Premultiplied)
        layer = ScientificLayer('image', 0, 0, (0, 0, 80, 64), (1., 0., 0., 1., 5.5, 6.5),
                                1., image=source, local_rect=(0., 0., 2., 2.))
        extent = SceneExtent(80, 64)
        self.assertEqual(sampled_image_scissor(layer, extent), (5, 56, 2, 2))
        self.assertEqual(sampled_image_scissor(replace(layer, sampling='qt611-nearest'), extent), (6, 55, 2, 2))
        with self.assertRaisesRegex(ValueError, 'sampling'):
            sampled_image_scissor(replace(layer, sampling='automatic'), extent)

    def test_unsupported_paths_reject_before_gl_operations(self):
        pixels = QImage(12, 7, QImage.Format.Format_RGBA8888_Premultiplied)
        layer = ScientificLayer('persistence', 0, 0, (0., 0., 80., 64.),
                                (.7, 0., 0., -6.25, 4.3, 64.5), 1., image=pixels,
                                local_rect=(0., 0., 12., 7.), source_format=3, sampling='qt611-nearest')
        extent = SceneExtent(80, 64)
        self.assertEqual(len(qt_sampling_inverse(layer, extent)), 4)
        for changes in (
            dict(source_format=6), dict(opacity=.5), dict(sampling='automatic'),
            dict(transform=(1., .1, 0., -2., 0., 0.)),
            dict(transform=(1., 0., 0., 1., 0., 0.)),
            dict(transform=(-1., 0., 0., -2., 0., 0.)),
            dict(transform=(.0001, 0., 0., -2., 0., 0.)),
            dict(transform=(.011, 0., 0., -2., 0., 0.)),
            dict(transform=(1., 0., 0., float('nan'), 0., 0.)),
            dict(local_rect=(0., 0., 0., 7.)), dict(clip=(0., 0., -1., 2.)),
            dict(image=QImage(12, 7, QImage.Format.Format_RGBA8888)), dict(image=None),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                invalid = replace(layer, **changes)
                bundle = ScientificLayers((invalid,), pixels.sizeInBytes(), 10000)
                if invalid.image is None:
                    qt_sampling_inverse(invalid, extent)
                else:
                    draw_scientific_gpu(None, None, extent, bundle)
        with patch('PySide6.QtCore.qVersion', return_value='6.12.0'), self.assertRaisesRegex(ValueError, '6.11.1'):
            qt_sampling_inverse(layer, extent)

    def test_native_actual_qhd_all_states_have_exact_image_layers(self):
        from tests.ui_v2.test_app05_full_composition import composition_probe
        target = SceneGpuTarget()
        available = target.available
        target.close()
        if not available:
            self.skipTest('requires native GL')
        report = composition_probe(Path(__file__).resolve().parents[2], native=True, review=True, qt_raster_compat=True)
        self.assertTrue(report['qt_raster_compat'])
        self.assertEqual(len(report['cases']), 4)
        for row in report['cases']:
            self.assertNotIn('gpu_declined', row)
            self.assertFalse(row['component_review']['product_accepted'])
            images = [command for command in row['component_review']['commands']
                      if command['scientific'] and command['scientific'].startswith(('persistence', 'waterfall'))]
            self.assertGreaterEqual(len(images), 2)
            for image in images:
                self.assertTrue(image['comparison']['equal'], (row['label'], image))


if __name__ == '__main__':
    unittest.main()
