from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.adapter import DflMeasurementAdapter
from esw_dfl.models import AcquisitionTiming, DflDocument, InstrumentInfo, SpectrogramInfo, TraceData


class DomainAdapterTests(unittest.TestCase):
    def test_parser_document_is_mapped_without_copying_axes_unnecessarily(self) -> None:
        trace = TraceData(
            key="trace:test:0", title="Trace 1", mode="Spectrum", measurement="Spectrum",
            measurement_type="Spectrum", source_stream="Application/All Traces", trace_index=0,
            x=np.linspace(100.0, 140.0, 5), y=np.array([-50, -40, -30, -40, -50], dtype=np.float32),
            x_unit="Hz", y_unit="dBm", detector="Positive Peak", update_mode="Max Hold", active=True,
        )
        info = SpectrogramInfo(
            key="spectrogram:test", title="Waterfall", mode="Spectrum", measurement="Spectrum",
            measurement_type="Spectrum", source_stream="Application/Spectrogram", line_count=100,
            point_count=5, start_hz=100.0, stop_hz=140.0,
        )
        document = DflDocument(
            path=Path("sample.dfl"), instrument=InstrumentInfo(device_type="ESW"),
            traces=[trace], spectrograms=[info], settings={"Spectrum": {"Rbw": 10.0}},
        )
        session = DflMeasurementAdapter().adapt(document)
        mapped = session.traces[trace.key]
        self.assertEqual(mapped.power_values.dtype, np.float32)
        self.assertIsNone(mapped.frequency_values)
        np.testing.assert_allclose(mapped.frequencies_hz, trace.x)
        self.assertEqual(mapped.rbw_hz, 10.0)
        self.assertEqual(mapped.source_stream, trace.source_stream)
        self.assertEqual(session.waterfalls[info.key].line_count, 100)

    def test_acquisition_timing_is_propagated_to_session(self) -> None:
        timing = AcquisitionTiming(
            mode="Spectrum", measurement="AnalyzerSweep",
            point_count=60001, rbw_hz=200000.0, vbw_hz=200000.0,
            instrument_sweep_time_s=0.0601,
        )
        document = DflDocument(
            path=Path("sample.dfl"), instrument=InstrumentInfo(device_type="ESW"),
            acquisition_timing={"Spectrum": timing},
        )
        session = DflMeasurementAdapter().adapt(document)
        mapped = session.acquisition_timing["Spectrum"]
        self.assertEqual(mapped.point_count, 60001)
        self.assertEqual(mapped.instrument_sweep_time_s, 0.0601)
        self.assertEqual(mapped.t_deadline_s, 0.0601)

    def test_session_is_visible_by_default(self) -> None:
        session = DflMeasurementAdapter().adapt(
            DflDocument(path=Path("x.dfl"), instrument=InstrumentInfo())
        )
        self.assertTrue(session.visible)

    def test_manual_marker_is_unlocked_by_default(self) -> None:
        from esw_dfl.domain import Marker, MarkerType
        marker = Marker()
        self.assertEqual(marker.marker_type, MarkerType.MANUAL)
        self.assertFalse(marker.locked)

    def test_peak_marker_is_locked_by_default(self) -> None:
        from esw_dfl.domain import Marker, MarkerType
        marker = Marker(marker_type=MarkerType.PEAK)
        self.assertTrue(marker.locked)


if __name__ == "__main__":
    unittest.main()

