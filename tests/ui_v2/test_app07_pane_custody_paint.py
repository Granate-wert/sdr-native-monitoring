"""Original Spectrum refs close only on exact display/paint boundaries."""
from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.test_app07_pane_delivery_obligations import identity


def frame(sequence):
    return SimpleNamespace(source_id="source", receiver_id="rx1", session_id="session",
        epoch=1, config_generation=1, sequence=sequence, unit="dBm",
        frequencies_hz=np.linspace(100e6, 101e6, 128),
        values=np.linspace(-100.0, -40.0, 128))


class PaneCustodyPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def retire_scene(self, scene):
        scene.release_graphics_after_shutdown()
        scene.close()
        scene.deleteLater()
        QCoreApplication.sendPostedEvents(scene, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(shiboken6.isValid(scene))

    def test_relevant_viewport_paint_returns_exact_ref_once(self):
        ref = PaneDeliveryLedger(("one",)).admit("one", identity())
        stages = []
        scene = SpectrumScene()
        scene.set_delivery_stage_callback(lambda actual, stage: stages.append((actual, stage)))
        scene.resize(720, 420)
        scene.set_frame(frame(1), obligation_ref=ref)
        scene.show()
        self.app.processEvents()
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertIn((ref, Stage.PAINT_SCHEDULED), stages)
        self.assertEqual(stages.count((ref, Stage.PAINT_RETURNED)), 1)
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertEqual(stages.count((ref, Stage.PAINT_RETURNED)), 1)
        self.retire_scene(scene)

    def test_confirmed_stop_detaches_pending_ref_without_clearing_pixels(self):
        ref = PaneDeliveryLedger(("one",)).admit("one", identity())
        stages = []
        scene = SpectrumScene()
        scene.set_delivery_stage_callback(lambda actual, stage: stages.append((actual, stage)))
        retained = frame(1)
        scene.set_frame(retained, obligation_ref=ref)
        self.assertIs(scene.latest_frame, retained)
        scene.stop_delivery_custody()
        self.assertIs(scene.latest_frame, retained)
        self.assertIsNone(scene.displayed_delivery_ref)
        self.assertEqual(stages[-1], (ref, Stage.STOP_CLEARED))
        self.retire_scene(scene)


if __name__ == "__main__":
    unittest.main()
