"""Sustained fake RTBW delivery through production composition; no hardware."""

from dataclasses import replace
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.i18n import UiLocale
import tests.test_app02_analyzer_workspace_product as product_fixture


class ZoomMarkerStreamTests(unittest.TestCase):
    def test_zoom_and_two_markers_survive_300_published_frames_and_locale(self):
        app = QApplication.instance() or QApplication([])
        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = app
        fixture.setUp()
        try:
            fixture.select_and_apply()
            fixture.page.primary.click()
            fixture.wait(lambda: fixture.live.is_running()
                         and not fixture.composition.view_model.state.busy)
            baseline = fixture.live.latest_snapshot()
            configuration = baseline.applied.applied
            frequencies = configuration.center_hz + (np.arange(4096) - 2048) * (61.44e6 / 4096)
            scene = fixture.page.visualization.spectrum_scene

            def publish(sequence):
                values = np.full(4096, -90.0, dtype=np.float32)
                values[1900], values[2200] = -60.0 + sequence / 1000, -70.0
                frame = LiveSpectrumFrame(
                    sequence=sequence, timestamp_ns=sequence * 1_000_000,
                    source_id="fake-pluto-usb", config_generation=baseline.generation,
                    center_frequency_hz=configuration.center_hz, sample_rate_hz=61.44e6,
                    fft_size=4096, hop_size=4096, frequencies_hz=frequencies,
                    values=values, unit="dBFS/bin",
                )
                snapshot = replace(baseline, spectrum=frame)
                fixture.live._snapshot = snapshot
                fixture.presenter.offer_snapshot_for_render(snapshot)
                fixture.wait(lambda: scene.latest_frame is not None
                             and scene.latest_frame.spectrum.sequence == sequence)
                return frame

            publish(1)
            scene.plot_item.setXRange(float(frequencies[1800]), float(frequencies[2300]), padding=0)
            scene.place_marker("M1", float(frequencies[1900]))
            scene.place_marker("M2", float(frequencies[2200]))
            target_range = tuple(scene.view_box.viewRange()[0])
            scene.set_reference_level(-30)
            scene.set_db_per_division(10)
            target_y_range = tuple(scene.view_box.viewRange()[1])
            for sequence in range(2, 302):
                if sequence == 151:
                    scene.set_vertical_lock(True)
                frame = publish(sequence)
                if sequence in (100, 200):
                    fixture.shell.select_appearance_locale(UiLocale.EN if sequence == 100 else UiLocale.RU)
                    app.processEvents()
                np.testing.assert_allclose(scene.view_box.viewRange()[0], target_range, rtol=0, atol=1e-5)
                self.assertEqual(tuple(scene.view_box.viewRange()[1]), target_y_range)
                self.assertEqual([marker.frequency_hz for marker in scene.markers],
                                 [float(frequencies[1900]), float(frequencies[2200])])
                self.assertAlmostEqual(scene.markers[0].value, float(frame.values[1900]), places=5)
                self.assertEqual(scene.markers[0].unit_label, "dBFS/bin")
                self.assertFalse(frame.values.flags.writeable)
            self.assertIs(fixture.page.visualization.spectrum_scene, scene)
            self.assertEqual(fixture.events, ["rtbw-start"])
        finally:
            try:
                fixture.tearDown()
            finally:
                fixture.doCleanups()
