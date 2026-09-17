"""Physical-walkthrough regressions: narrow-span labels and Windows layout."""

from types import SimpleNamespace
import sys
import unittest

import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.spectrum.axis import FrequencyAxis
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


class FrequencyPrecisionKeysTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_zoomed_axis_labels_distinguish_adjacent_ticks_in_both_locales(self):
        for locale in UiLocale:
            axis = FrequencyAxis(orientation="bottom", locale=locale)
            for spacing in (200_000., 1000., 1.):
                values = [2.4e9 + index * spacing for index in range(4)]
                labels = axis.tickStrings(values, 1., spacing)
                self.assertEqual(len(set(labels)), 4)
            axis.deleteLater()

    @unittest.skipUnless(sys.platform == "win32", "Windows virtual-key mapping")
    def test_russian_layout_peak_shortcuts_and_control_modifier_guard(self):
        scene = SpectrumScene()
        scene.set_frame(SimpleNamespace(frequencies_hz=np.arange(6, dtype=float) + 2.4e9,
                                       values=np.array([-90., -50., -90., -60., -90., -95.]),
                                       unit="dBFS/bin"))
        scene.place_marker("M1", 2.4e9)
        def press(key, native, modifiers=Qt.KeyboardModifier.NoModifier):
            event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, 0, native, 0)
            scene.keyPressEvent(event)
        press(0x0417, 0x50)  # Cyrillic З on physical P.
        self.assertEqual(scene.markers[0].frequency_hz, 2.4e9 + 1)
        press(0x042A, 0xDD)  # Cyrillic Ъ on physical ].
        self.assertEqual(scene.markers[0].frequency_hz, 2.4e9 + 3)
        press(0x0417, 0x50, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(scene.markers[0].frequency_hz, 2.4e9 + 3)
        scene.close()
        scene.deleteLater()
