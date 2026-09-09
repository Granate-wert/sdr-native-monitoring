"""One V2 renderer consumes both actual domain publication variants."""
import os
import time
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import AnalyzerPublicationKind, bundle_from_live, bundle_from_sweep
from sdr_monitor.domain.live import LiveSnapshot, LiveSpectrumFrame, LiveSessionState
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.native_continuous_sweep import (
    ContinuousSweepDisplaySnapshot, ContinuousSweepDisplayMetrics,
)
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind


class AnalyzerBundleSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rtbw_then_gapped_sweep_use_the_same_scene(self):
        live = LiveSpectrumFrame(
            sequence=1, timestamp_ns=1, center_frequency_hz=101.,
            sample_rate_hz=256., fft_size=256, hop_size=128,
            frequencies_hz=np.array([100., 101., 102.]),
            values=np.array([-80., -40., -70.]), unit="dBFS/Hz",
        )
        sweep = SweepLineFrame(
            sequence=1, epoch=2, completed_at_ns=900, source_id="sweep",
            state=SweepLineState.GAP, frequencies_hz=np.array([200., 201., 202.]),
            values_db=np.array([-90., np.nan, -30.]),
            quality_flags=np.array([0, 1, 0], dtype=np.uint16),
            source_segment_indices=np.array([0, -1, 1]),
            missing_segment_indices=(2,), segment_config_generations=((0, 1), (1, 2)),
            gap_reasons=(), unit="dBFS/bin",
        )
        bundles = (
            bundle_from_live(LiveSnapshot(
                generation=1, sequence=1, state=LiveSessionState.RUNNING, spectrum=live,
            )),
            bundle_from_sweep(sweep),
        )
        scene = SpectrumScene()
        try:
            for bundle in bundles:
                scene.set_frame(bundle)
                envelope = scene.trace_envelope(TraceKind.CURRENT)
                np.testing.assert_array_equal(envelope.values, bundle.values)
                np.testing.assert_array_equal(envelope.frequencies_hz, bundle.frequencies_hz)
                self.assertEqual(scene._latest_view.unit_label, bundle.unit)
                self.assertIs(scene._latest_view.source_frame, bundle)
            self.assertTrue(np.isnan(scene.trace_envelope(TraceKind.CURRENT).values[1]))
        finally:
            scene.close()
            scene.deleteLater()

    def test_presenter_renders_progress_before_terminal_line(self):
        frequency = np.array([200., 201., 202.])
        values = np.array([-90., -80., np.nan], dtype=np.float32)
        quality = np.array([0, 0, 4096], dtype=np.uint32)
        indices = np.array([0, 0, -1], dtype=np.int32)
        for array in (frequency, values, quality, indices):
            array.setflags(write=False)
        progress = SweepProgressFrame(
            source_id="test", sequence=1, epoch=2, revision=1, unit="dBFS/bin",
            frequencies_hz=frequency, values_db=values, quality_flags=quality,
            source_segment_indices=indices,
            acquired_segment_generations=((0, 1),), pending_segment_indices=(1,),
        )
        class Service:
            def start(self, _config):
                pass

            def poll_latest(self):
                return ContinuousSweepDisplaySnapshot(
                    None, ContinuousSweepDisplayMetrics(), progress,
                )

            def stop(self):
                pass

            def close(self):
                pass

        presenter = ContinuousSweepPresenter(Service())
        scene = SpectrumScene()
        lines = []
        presenter.line_ready.connect(lines.append)
        presenter.analyzer_ready.connect(scene.set_frame)
        try:
            presenter.start(object())
            deadline = time.monotonic() + 1
            while presenter.is_starting and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.001)
            self.assertFalse(presenter.is_starting)
            presenter._poll()
            self.assertEqual(lines, [])
            envelope = scene.trace_envelope(TraceKind.CURRENT)
            np.testing.assert_array_equal(envelope.values, values)
            self.assertEqual(scene._latest_view.source_frame.spectrum.revision, 1)
            self.assertIs(scene._latest_view.source_frame.publication_kind,
                          AnalyzerPublicationKind.SWEEP_PROGRESS)
            self.assertFalse(scene._latest_view.source_frame.terminal_sweep)
            self.assertEqual(scene._latest_view.unit_label, "dBFS/bin")
        finally:
            presenter.shutdown()
            scene.close()
            scene.deleteLater()
