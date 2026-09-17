"""CPU DSP -> native assembler -> both public bridges -> one V2 canvas.

Synthetic I/Q and fake device lifecycle only; no RX/EXE/performance evidence.
"""

from dataclasses import replace
import itertools
import unittest
from unittest.mock import patch

import numpy as np

from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import CONTRACT_SCHEMA_VERSION
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.services.native_continuous_sweep import _to_domain_line
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
from tests.test_native_live_discovery import _FakeNative


class SingleWindowParityTests(unittest.TestCase):
    def test_compiled_values_grid_and_markers_agree_across_explicit_mode_switch(self):
        native = native_api.require_native()
        profiles = itertools.product(
            (native.WindowType.RECTANGULAR, native.WindowType.HANN),
            (native.SpectrumUnit.DBFS_BIN, native.SpectrumUnit.DBFS_HZ), (1, 4),
        )
        for window, unit, average in profiles:
            with self.subTest(window=window, unit=unit, averaging=average):
                harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
                harness.setUpClass()
                harness.setUp()
                try:
                    harness.select_and_apply()
                    page = harness.page
                    canvas = page.visualization
                    scene = canvas.spectrum_scene
                    page.primary.click()
                    harness.wait(lambda: harness.live.is_running() and not harness.composition.view_model.state.busy)
                    baseline = harness.live.latest_snapshot()
                    config = baseline.applied.applied
                    size = config.fft_size
                    backend = native.CpuDspBackend()
                    detector = native.DetectorType.SAMPLE if average == 1 else native.DetectorType.AVERAGE_POWER
                    backend.configure(native.DspConfig(
                        size, size, window, detector, unit, native.PrecisionMode.REFERENCE_F64,
                        1, average, 8.6, native.CalibrationStatus.UNCALIBRATED, "", CONTRACT_SCHEMA_VERSION,
                    ))
                    t = np.arange(size * average)
                    samples = np.asarray(.5 * np.exp(2j * np.pi * 73 * t / size), dtype=np.complex64)
                    backend.push_samples(samples, config.sample_rate_hz, config.center_hz)
                    raw = backend.poll_spectrum(0)[-1]
                    raw_line = native._make_test_single_segment_line(raw, size // 4, size // 2)
                    line = _to_domain_line(raw_line)
                    bridge = NativeLiveSessionService(_FakeNative())
                    self.assertTrue(bridge._publish_frame(raw))
                    original = bridge.latest_snapshot().spectrum
                    # Only fake-session routing identity changes, never numbers,
                    # geometry, producer timestamps or numerical provenance.
                    live = replace(original, source_id=baseline.device.device_id,
                                   config_generation=baseline.generation)
                    snapshot = replace(baseline, spectrum=live)
                    harness.live._snapshot = snapshot
                    harness.presenter.offer_snapshot_for_render(snapshot)
                    harness.wait(lambda: scene.latest_frame is not None)
                    self.assertIs(scene.latest_frame.spectrum, live)
                    crop = slice(size // 4, 3 * size // 4)
                    np.testing.assert_array_equal(line.frequencies_hz, live.frequencies_hz[crop])
                    np.testing.assert_allclose(line.values_db, live.values[crop], rtol=0, atol=1e-4)
                    self.assertEqual(line.unit, live.unit)
                    self.assertFalse(line.values_db.flags.writeable)
                    self.assertFalse(original.values.flags.writeable)
                    acquisition = line.segment_acquisition[0]
                    self.assertEqual(acquisition.frame_sequence, original.sequence)
                    self.assertEqual(acquisition.timestamp_ns, original.timestamp_ns)
                    self.assertEqual(acquisition.config_generation, original.config_generation)
                    self.assertEqual(original.numerical_provenance.averaging_frames, average)
                    frequency = float(live.frequencies_hz[np.argmax(live.values)])
                    scene.place_marker("M1", frequency)
                    live_marker = scene.markers[0]
                    page.primary.click()
                    harness.wait(lambda: not harness.live.is_running() and not harness.composition.view_model.state.busy)
                    page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
                    self.assertIsNone(scene.latest_frame)
                    self.assertEqual(scene.markers, ())
                    sweep = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics())
                    with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=sweep):
                        page.primary.click()
                        harness.wait(lambda: page._last_bundle is not None and page._last_bundle.mode == "sweep")
                        self.assertIs(page.visualization, canvas)
                        self.assertIs(canvas.spectrum_scene, scene)
                        np.testing.assert_array_equal(page._last_bundle.values, line.values_db)
                        scene.place_marker("M1", frequency)
                        self.assertAlmostEqual(scene.markers[0].value, live_marker.value, delta=1e-4)
                        self.assertEqual(scene.markers[0].unit_label, live_marker.unit_label)
                        page.primary.click()
                        harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
                    self.assertEqual(harness.events, ["rtbw-start", "rtbw-stop", "sweep-start", "sweep-stop"])
                    # Publication and display must not mutate retained input.
                    np.testing.assert_array_equal(original.values, raw.values)
                    for first, count in ((0, 0), (0, 255), (0, 257), (size, 256), (size - 1, 256)):
                        with self.assertRaises(ValueError):
                            native._make_test_single_segment_line(raw, first, count)
                finally:
                    try:
                        harness.tearDown()
                    finally:
                        harness.doCleanups()
