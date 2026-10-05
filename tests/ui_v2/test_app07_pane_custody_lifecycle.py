"""Per-scene custody clear and Stop boundaries preserve peer isolation."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
import os

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.test_app07_pane_delivery_obligations import identity


class PaneCustodyLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_confirmed_stop_and_actual_clear_are_pane_local(self):
        first_ref = PaneDeliveryLedger(("one",)).admit("one", identity())
        peer_ref = PaneDeliveryLedger(("two",)).admit("two", identity("two"))
        first_stages = []
        peer_stages = []
        first = SpectrumScene()
        peer = SpectrumScene()
        first.set_delivery_stage_callback(lambda ref, stage: first_stages.append((ref, stage)))
        peer.set_delivery_stage_callback(lambda ref, stage: peer_stages.append((ref, stage)))
        retained = SimpleNamespace(source_id="source", epoch=1, sequence=1, unit="dBm",
            frequencies_hz=np.array([100.0, 101.0]), values=np.array([-80.0, -70.0]))
        first.set_frame(retained, obligation_ref=first_ref)
        peer.set_frame(SimpleNamespace(source_id="peer", epoch=1, sequence=1, unit="dBm",
            frequencies_hz=np.array([100.0, 101.0]), values=np.array([-90.0, -75.0])), obligation_ref=peer_ref)

        first.stop_delivery_custody()
        self.assertEqual(first_stages[-1], (first_ref, Stage.STOP_CLEARED))
        self.assertIs(first.latest_frame, retained)
        self.assertEqual(peer_stages[-1], (peer_ref, Stage.PAINT_SCHEDULED))
        self.assertEqual(peer.latest_frame.sequence, 1)

        peer.clear_measurement()
        self.assertEqual(peer_stages[-1], (peer_ref, Stage.PAINT_SUPERSEDED))
        self.assertIsNone(peer.latest_frame)
        first.close()
        peer.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
