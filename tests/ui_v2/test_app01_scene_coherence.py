"""Offscreen accumulated-layer reset checks for APP-01."""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum import TraceKind
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


def _frame(source: str, *, epoch: int = 1, unit: str = "dBFS/Hz") -> SimpleNamespace:
    frequencies = np.array((100.0, 101.0), dtype=np.float64)
    values = np.array((-80.0, -70.0), dtype=np.float32)
    frequencies.setflags(write=False)
    values.setflags(write=False)
    identity = SimpleNamespace(
        source_id=source, receiver_id="rx", acquisition_epoch=epoch,
        config_generation=4,
    )
    return SimpleNamespace(
        frequencies_hz=frequencies, values=values, unit=unit, identity=identity,
    )


class SceneCoherenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_identity_change_clears_holds_but_same_identity_keeps_them(self) -> None:
        scene = SpectrumScene()
        first = _frame("a")
        scene.set_frame(first)
        scene.set_trace(TraceKind.AVERAGE, first)
        scene.set_frame(_frame("a"))
        self.assertIsNotNone(scene.trace_envelope(TraceKind.AVERAGE))
        scene.set_frame(_frame("b"))
        self.assertIsNone(scene.trace_envelope(TraceKind.AVERAGE))
        self.assertIsNotNone(scene.trace_envelope(TraceKind.CURRENT))


if __name__ == "__main__":
    unittest.main()
