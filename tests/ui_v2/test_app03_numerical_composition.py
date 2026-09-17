"""Compiled synthetic DSP -> native Live bridge -> actual V2 composition."""

from dataclasses import replace
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import CONTRACT_SCHEMA_VERSION
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.ui.v2.state.analyzer_readouts import spectrum_numerical_readout
from tests.test_native_live_discovery import _FakeNative
import tests.test_app02_analyzer_workspace_product as product_fixture


class NumericalCompositionTests(unittest.TestCase):
    def test_native_semantics_reach_canvas_and_accessible_description(self):
        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = QApplication.instance() or QApplication([])
        fixture.setUp()
        try:
            fixture.select_and_apply()
            fixture.page.primary.click()
            fixture.wait(lambda: fixture.live.is_running() and not fixture.composition.view_model.state.busy)
            baseline = fixture.live.latest_snapshot()
            config = baseline.applied.applied
            native = native_api.require_native()
            backend = native.CpuDspBackend()
            size = config.fft_size
            backend.configure(native.DspConfig(
                size, size, native.WindowType.HANN, native.DetectorType.SAMPLE,
                native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64,
                1, 1, 8.6, native.CalibrationStatus.UNCALIBRATED, "", CONTRACT_SCHEMA_VERSION,
            ))
            samples = np.asarray(.5 * np.exp(2j * np.pi * 73 * np.arange(size) / size), dtype=np.complex64)
            backend.push_samples(samples, config.sample_rate_hz, config.center_hz)
            bridge = NativeLiveSessionService(_FakeNative())
            self.assertTrue(bridge._publish_frame(backend.poll_spectrum(0)[0]))
            # The fixture owns a fake device/session; only identity is rebound.
            # Numerical arrays and producer metadata are untouched.
            frame = replace(bridge.latest_snapshot().spectrum, source_id=baseline.device.device_id,
                            config_generation=baseline.generation)
            snapshot = replace(baseline, spectrum=frame)
            fixture.live._snapshot = snapshot
            fixture.presenter.offer_snapshot_for_render(snapshot)
            scene = fixture.page.visualization.spectrum_scene
            fixture.wait(lambda: scene.latest_frame is not None)
            self.assertIs(scene.latest_frame.spectrum, frame)
            self.assertEqual(frame.numerical_provenance.window, "hann")
            self.assertIn(spectrum_numerical_readout(frame), fixture.page.applied.toolTip())
            self.assertEqual(fixture.page.applied.toolTip(), fixture.page.applied.accessibleDescription())
            peak = int(np.argmax(frame.values))
            scene.place_marker("M1", float(frame.frequencies_hz[peak]))
            self.assertAlmostEqual(scene.markers[0].value, 20 * np.log10(.5), delta=5e-5)
            self.assertEqual(scene.markers[0].unit_label, "dBFS/bin")
        finally:
            try:
                fixture.tearDown()
            finally:
                fixture.doCleanups()
