from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

QApplication: Any = None
MainWindow: Any = None
MeasurementMetadata: Any = None
MeasurementSession: Any = None
WaterfallData: Any = None
AcquisitionTiming: Any = None

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication as QApplicationClass, QDockWidget
    from esw_dfl.domain import MeasurementMetadata as MD, MeasurementSession as MS, WaterfallData as WD
    from esw_dfl.gui import MainWindow as MW
    from esw_dfl.models import AcquisitionTiming as AT
    from esw_dfl.smoothing import (
        SpectrumSmoothMethod,
        SpectrumSmoothSettings,
        WaterfallSmoothMethod,
        WaterfallSmoothSettings,
    )
    from heatmap_test_isolation import shutdown_window

    QApplication = QApplicationClass
    MainWindow = MW
    MeasurementMetadata = MD
    MeasurementSession = MS
    WaterfallData = WD
    AcquisitionTiming = AT
except ImportError:
    SpectrumSmoothMethod = SpectrumSmoothSettings = WaterfallSmoothMethod = WaterfallSmoothSettings = None  # type: ignore
    pass


@unittest.skipIf(QApplication is None, "PySide6 не установлен")
class QtGuiTests(unittest.TestCase):
    app: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_has_required_docks_and_pyqtgraph_views(self) -> None:
        window = MainWindow()
        self.addCleanup(shutdown_window, window, self.app)
        names = {dock.objectName() for dock in window.findChildren(QDockWidget)}
        self.assertTrue({
            "filesTracesDock", "markersDock", "measurementsDock", "propertiesDock",
            "displayDock", "waterfallSettingsDock", "playbackDock", "eventsDock",
            "logDock", "metadataDock", "channelPowerDock", "channelPowerTimeDock",
        }.issubset(names))
        self.assertIsNotNone(window.spectrum_renderer.widget)
        self.assertIsNotNone(window.waterfall_renderer.widget)
        self.assertEqual(window.events_table.columnCount(), 11)
        self.assertEqual(window.frame_spin.minimum(), 1)
        self.assertIsNotNone(window.view_settings_action)
        self.assertIsNotNone(window.cp_raw_curve)
        self.assertEqual(window.power_measurement_mode.count(), 11)
        self.assertEqual(window.power_profile.count(), 28)
        self.assertEqual(window.power_regions_table.columnCount(), 9)
        self.assertEqual(window.power_measurement_mode.currentText(), "Time-Gated Channel Power")
        self.assertEqual(window.playback_timer.timerType(), Qt.TimerType.PreciseTimer)
        self.assertEqual(
            window._frame_scheduler._timer.timerType(), Qt.TimerType.PreciseTimer
        )
        self.assertEqual(
            window._heatmap_controller._render_timer.timerType(),
            Qt.TimerType.PreciseTimer,
        )

    def test_playback_interval_prefers_timing_deadline(self) -> None:
        window = MainWindow()
        self.addCleanup(shutdown_window, window, self.app)
        timing = AcquisitionTiming(
            mode="Spectrum", measurement="AnalyzerSweep",
            instrument_sweep_time_s=0.100,
        )
        session = MeasurementSession(
            "test-id", Path("x.dfl"), "x", MeasurementMetadata(),
            acquisition_timing={"Spectrum": timing},
        )
        session.waterfalls["wf"] = WaterfallData(
            "wf", "WF", 10, 1001, 1e9, 2e9, 1e6, "stream",
            metadata={"mode": "Spectrum"},
        )
        session.active_waterfall_id = "wf"
        window.repository.add(session)
        window.active_session_id = "test-id"
        window.speed_combo.setCurrentText("1×")
        window.fps_combo.setCurrentText("30")
        window.no_skip_check.setChecked(True)
        window._update_playback_interval()
        self.assertAlmostEqual(window.playback_timer.interval(), 100, delta=2)

    def test_peak_marker_follows_current_frame(self) -> None:
        from esw_dfl.domain import Marker, MarkerType, SpectrumTrace
        from esw_dfl.spectrogram import SpectrogramRow

        window = MainWindow()
        self.addCleanup(shutdown_window, window, self.app)
        session = MeasurementSession(
            "peak-test", Path("x.dfl"), "x", MeasurementMetadata()
        )
        frequencies = np.linspace(1e9, 2e9, 1001)
        values = np.full_like(frequencies, -80.0)
        peak_index = 500
        values[peak_index] = -30.0
        trace = SpectrumTrace(
            "trace-1", "Trace 1", frequencies[0], frequencies[-1],
            float(np.diff(frequencies).mean()), values, frequency_values=frequencies,
        )
        session.traces["trace-1"] = trace
        session.active_trace_id = "trace-1"
        waterfall = WaterfallData(
            "wf", "WF", 10, 1001, frequencies[0], frequencies[-1],
            float(np.diff(frequencies).mean()), "stream",
            values=np.zeros((10, 1001), dtype=np.float32),
        )
        session.waterfalls["wf"] = waterfall
        session.active_waterfall_id = "wf"
        window.repository.add(session)
        window.active_session_id = "peak-test"
        window.spectrum_renderer.set_trace(trace)

        marker = Marker(name="P1", marker_type=MarkerType.PEAK, trace_id="trace-1")
        marker.frequency_hz = 1.1e9
        marker.power = -80.0
        session.markers.append(marker)
        window.spectrum_renderer.set_marker(marker)

        row_values = values.copy()
        row_values[400] = -20.0
        row = SpectrogramRow(line_index=3, timestamp=0.0, values=row_values.astype(np.float32))
        window._display_exact_frame(session, waterfall, 3, row)

        self.assertAlmostEqual(marker.frequency_hz, frequencies[400], delta=1e3)
        self.assertAlmostEqual(marker.power, -20.0, delta=0.1)

    def test_spectrum_interpolation_increases_displayed_points(self) -> None:
        from esw_dfl.domain import SpectrumTrace

        window = MainWindow()
        self.addCleanup(shutdown_window, window, self.app)
        session = MeasurementSession(
            "smooth-test", Path("x.dfl"), "x", MeasurementMetadata()
        )
        x = np.linspace(1e9, 2e9, 1001)
        y = np.full(1001, -80.0, dtype=np.float32)
        y[500] = -30.0
        trace = SpectrumTrace(
            "t1", "T1", x[0], x[-1], float(np.diff(x).mean()), y,
            frequency_values=x,
        )
        session.traces["t1"] = trace
        session.active_trace_id = "t1"
        window.repository.add(session)
        window.active_session_id = "smooth-test"
        window.spectrum_renderer.set_trace(trace)
        window.spectrum_renderer.plot.setXRange(1.45e9, 1.55e9, padding=0)
        raw_count = int(window.spectrum_renderer.trace_data("t1")[0].size)

        window.spectrum_renderer.set_smoothing(
            SpectrumSmoothSettings(
                method=SpectrumSmoothMethod.PCHIP, auto_zoom=False
            )
        )
        interp_count = int(window.spectrum_renderer.trace_data("t1")[0].size)
        self.assertGreater(interp_count, raw_count)

        window.spectrum_renderer.set_smoothing(
            SpectrumSmoothSettings(method=SpectrumSmoothMethod.NONE)
        )
        restored_count = int(window.spectrum_renderer.trace_data("t1")[0].size)
        self.assertEqual(restored_count, raw_count)

    def test_spectrum_peak_marker_uses_raw_data(self) -> None:
        from esw_dfl.domain import SpectrumTrace

        window = MainWindow()
        self.addCleanup(shutdown_window, window, self.app)
        session = MeasurementSession(
            "peak-test", Path("x.dfl"), "x", MeasurementMetadata()
        )
        x = np.linspace(1e9, 2e9, 1001)
        y = np.full(1001, -80.0, dtype=np.float32)
        y[500] = -30.0
        trace = SpectrumTrace(
            "t1", "T1", x[0], x[-1], float(np.diff(x).mean()), y,
            frequency_values=x,
        )
        session.traces["t1"] = trace
        session.active_trace_id = "t1"
        window.repository.add(session)
        window.active_session_id = "peak-test"
        window.spectrum_renderer.set_trace(trace)
        window.spectrum_renderer.plot.setXRange(1.45e9, 1.55e9, padding=0)
        window.spectrum_renderer.set_smoothing(
            SpectrumSmoothSettings(
                method=SpectrumSmoothMethod.MAKIMA, auto_zoom=False
            )
        )
        session.markers.clear()
        window.add_peak_marker()
        self.assertEqual(len(session.markers), 1)
        self.assertAlmostEqual(session.markers[0].frequency_hz, x[500], delta=1e3)
        self.assertAlmostEqual(session.markers[0].power, -30.0, delta=0.5)

    def test_waterfall_interpolation_flag_toggles(self) -> None:
        window = MainWindow()
        self.addCleanup(shutdown_window, window, self.app)
        waterfall = WaterfallData(
            "wf", "WF", 10, 1001, 1e9, 2e9, 1e6, "stream",
            values=np.full((10, 1001), -100.0, dtype=np.float32),
        )
        window.waterfall_renderer.set_data(waterfall)
        self.assertFalse(window.waterfall_renderer.image._smooth)

        window.waterfall_renderer.set_smoothing(
            WaterfallSmoothSettings(
                method=WaterfallSmoothMethod.BILINEAR, auto_zoom=False
            )
        )
        self.assertTrue(window.waterfall_renderer.image._smooth)


if __name__ == "__main__":
    unittest.main()
