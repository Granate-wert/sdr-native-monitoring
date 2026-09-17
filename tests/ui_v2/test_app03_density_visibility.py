"""Fixed logarithmic display transfer keeps rare density visible and truthful."""

import unittest

import numpy as np

from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    DensityValueMode, PersistenceDensityFrame, adapt_persistence_density,
    map_density_for_display, map_density_row_for_display,
)


class DensityVisibilityTests(unittest.TestCase):
    def test_fixed_transfer_is_monotonic_preserves_endpoints_and_source(self):
        values = np.array([[0, 0.0001, 0.001, 0.01, 0.068, 0.1, 1]], dtype=np.float32)
        values.setflags(write=False)
        frame = PersistenceDensityFrame(values, np.arange(8.), np.array([-100., -90.]),
                                        DensityValueMode.PROBABILITY, "dBFS/bin")
        view = adapt_persistence_density(frame)
        mapped = map_density_for_display(view, logarithmic=True)
        np.testing.assert_allclose(mapped, np.log1p(9999 * values) / np.log(10000), rtol=1e-6)
        self.assertEqual(float(mapped[0, 0]), 0)
        self.assertEqual(float(mapped[0, -1]), 1)
        self.assertTrue(np.all(np.diff(mapped[0]) > 0))
        self.assertGreater(float(mapped[0, 4]), 0.7)
        np.testing.assert_array_equal(map_density_for_display(view, logarithmic=False), values)
        self.assertIs(frame.density, values)
        self.assertFalse(values.flags.writeable)
        row = np.empty(7, dtype=np.float32)
        map_density_row_for_display(values[0], value_mode=DensityValueMode.PROBABILITY,
                                    logarithmic=True, count_maximum=1, out=row)
        np.testing.assert_array_equal(row, mapped[0])

    def test_mapping_does_not_change_when_unrelated_peak_changes(self):
        def mapped(peak):
            values = np.array([[0.01, peak]], dtype=np.float32)
            frame = PersistenceDensityFrame(values, np.arange(3.), np.array([-100., -90.]),
                                            DensityValueMode.PROBABILITY, "dBFS/bin")
            return map_density_for_display(adapt_persistence_density(frame), logarithmic=True)[0, 0]
        self.assertEqual(mapped(0.1), mapped(1.0))

    def test_count_transfer_uses_explicit_frame_maximum(self):
        values = np.array([[0, 1, 10, 100]], dtype=np.float32)
        values.setflags(write=False)
        frame = PersistenceDensityFrame(values, np.arange(5.), np.array([-100., -90.]),
                                        DensityValueMode.COUNT, "dBFS/bin")
        mapped = map_density_for_display(adapt_persistence_density(frame), logarithmic=True)
        expected = np.log1p(9999 * (values / 100)) / np.log(10000)
        np.testing.assert_allclose(mapped, expected, rtol=1e-6)
        self.assertFalse(values.flags.writeable)

    def test_log_tooltip_follows_locale_and_explains_count_normalization(self):
        from PySide6.QtWidgets import QApplication
        from sdr_monitor.ui.v2.i18n import UiLocale, text
        from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene

        app = QApplication.instance() or QApplication([])
        scene = SpectrumScene()
        try:
            for locale in (UiLocale.EN, UiLocale.RU, UiLocale.EN):
                scene.set_locale(locale)
                tooltip = scene._persistence_log.toolTip()
                self.assertEqual(tooltip, text("spectrum.persistence.log.help", locale))
                self.assertIn("frame maximum" if locale is UiLocale.EN else "максимума кадра", tooltip)
                self.assertIn("9999", tooltip)
        finally:
            scene.close()
            scene.deleteLater()
            app.processEvents()
