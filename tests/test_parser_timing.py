"""Unit tests for mode-specific acquisition timing extraction.

These tests verify the structural settings parser that replaced the earlier
flat ``SweepTime`` extraction. They run with mocked olefile I/O so they do not
need real DFL files.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import olefile
import numpy as np

from esw_dfl.models import AcquisitionTiming, FramePeriodStatistics, MeasurementQuality
from esw_dfl.parser import DflParser


class MockOleStream:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size >= len(self._data):
            chunk, self._data = self._data, b""
            return chunk
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def seek(self, offset: int, whence: int = 0) -> None:
        if whence == 0:
            self._data = self._data[offset:]
        elif whence == 2:
            self._data = b""

    def tell(self) -> int:
        return 0


class MockOleFileIO:
    def __init__(self, path: str | Path, streams: dict[str, bytes]) -> None:
        self._streams = streams

    def listdir(self, streams: bool = True, storages: bool = False) -> list[list[str]]:
        return [name.split("/") for name in self._streams]

    def openstream(self, name: str) -> MockOleStream:
        data = self._streams.get(name)
        if data is None:
            raise OSError(f"Stream not found: {name}")
        return MockOleStream(data)

    def get_size(self, name: str) -> int:
        return len(self._streams.get(name, b""))

    def __enter__(self) -> "MockOleFileIO":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _build_current_settings(
    measurement: str,
    sweep_time_s: float,
    rbw_hz: float,
    vbw_hz: float,
    meas_points: int | None = None,
    duplicate_sweep_time: float | None = None,
) -> bytes:
    """Build a Current Settings XML with mode-specific and decoy settings."""
    group_name = "AnalyzerSweep" if measurement == "AnalyzerSweep" else "RealTime"
    meas_points_xml = (
        f"""
        <Group Name="MeasPoints">
          <Prop Name="Value" Value="{meas_points}" UnitId="NONE"/>
          <Prop Name="AutoMode" Value="Off"/>
        </Group>"""
        if meas_points is not None
        else ""
    )
    decoy_xml = ""
    if duplicate_sweep_time is not None:
        decoy_xml = f"""
        <Group Name="Trigger">
          <Prop Name="SweepTime" Value="{duplicate_sweep_time}" UnitId="FREQ_SEC"/>
        </Group>
        <Group Name="CommonRepository">
          <Prop Name="SweepTime" Value="{duplicate_sweep_time}" UnitId="FREQ_SEC"/>
        </Group>"""

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Settings>
  <Root>
    <Prop Name="Measurement" Value="{measurement}"/>
    <Group Name="{group_name}">
      <Group Name="ResolutionBandwidth">
        <Prop Name="Value" Value="{rbw_hz}" UnitId="FREQ_HZ"/>
        <Prop Name="AutoMode" Value="On"/>
      </Group>
      <Group Name="VideoBandwidth">
        <Prop Name="Value" Value="{vbw_hz}" UnitId="FREQ_HZ"/>
        <Prop Name="AutoMode" Value="On"/>
      </Group>
      <Group Name="SweepTime">
        <Prop Name="Value" Value="{sweep_time_s}" UnitId="FREQ_SEC"/>
        <Prop Name="AutoMode" Value="Off"/>
      </Group>
      {meas_points_xml}
    </Group>
    {decoy_xml}
  </Root>
</Settings>
"""
    return xml.encode("utf-8")


def _build_header() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8"?>
<Header>
  <DeviceType Value="ESW"/>
  <FirmwareVersion Value="3.0"/>
  <System Value="Analyzer"/>
</Header>
"""


def _build_all_traces(mode: str = "Spectrum") -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8"?>
<Traces>
  <Root>
    <Prop Name="Measurement" Value="AnalyzerSweep"/>
    <Prop Name="MeasurementType" Value="Spectrum"/>
    <Group Name="Trace">
      <Prop Name="ActiveIdx" Value="0"/>
      <ArrItem Index="0">
        <Prop Name="State" Value="Active"/>
        <Prop Name="Detector" Value="Positive Peak"/>
        <Prop Name="UpdateMode" Value="Clear/Write"/>
        <Group Name="TraceResult">
          <Prop Name="TraceLength" Value="3"/>
          <Prop Name="XValue" Block="0" Start="1000000000" Stop="1100000000" BlockItems="0" UnitId="FREQ_HZ"/>
          <Prop Name="YValue" Block="0" Start="-50" Stop="-40" BlockItems="0" UnitId="LEVEL_DBM"/>
        </Group>
      </ArrItem>
    </Group>
  </Root>
</Traces>
"""


def _build_spectrogram_stream(point_count: int = 3, start_hz: float = 0.0, stop_hz: float = 0.0) -> bytes:
    import base64

    values = np.zeros(point_count, dtype="<f4")
    payload = base64.b64encode(values.tobytes()).decode("ascii")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Settings>
  <Root>
    <Prop Name="Measurement" Value="AnalyzerSweep"/>
    <Prop Name="MeasurementType" Value="Spectrum"/>
  </Root>
  <SgramLine Line="0" Start="{start_hz}" Stop="{stop_hz}" Timestamp="2026-07-10T12:00:00Z">
    <DataBlock Block="0" Data="{payload}"/>
  </SgramLine>
</Settings>
""".encode("utf-8")


class StructuralTimingTests(unittest.TestCase):
    def _parse_with_streams(self, streams: dict[str, bytes]) -> dict[str, AcquisitionTiming]:
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".dfl") as tmp:
            tmp.write(b"dummy")
            tmp_path = tmp.name
        try:
            with patch.object(olefile, "isOleFile", return_value=True), patch.object(
                olefile, "OleFileIO", side_effect=lambda path, *args, **kwargs: MockOleFileIO(path, streams)
            ):
                doc = DflParser().parse(tmp_path)
            return doc.acquisition_timing
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _parse_doc_with_streams(self, streams: dict[str, bytes]) -> Any:
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".dfl") as tmp:
            tmp.write(b"dummy")
            tmp_path = tmp.name
        try:
            with patch.object(olefile, "isOleFile", return_value=True), patch.object(
                olefile, "OleFileIO", side_effect=lambda path, *args, **kwargs: MockOleFileIO(path, streams)
            ):
                return DflParser().parse(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_swept_60k_point_extracts_correct_sweep_time(self) -> None:
        """Regression: trigger SweepTime=1 must not shadow AnalyzerSweep value."""
        streams = {
            "Header": _build_header(),
            "Application/FSLSpectrumAnalyzer/Spectrum/Current Settings": _build_current_settings(
                measurement="AnalyzerSweep",
                sweep_time_s=0.0601,
                rbw_hz=200000.0,
                vbw_hz=200000.0,
                meas_points=60001,
                duplicate_sweep_time=1.0,
            ),
            "Application/FSLSpectrumAnalyzer/Spectrum/All Traces": _build_all_traces(),
        }
        timing = self._parse_with_streams(streams)["Spectrum"]
        self.assertEqual(timing.measurement, "AnalyzerSweep")
        self.assertEqual(timing.point_count, 60001)
        self.assertEqual(timing.rbw_hz, 200000.0)
        self.assertEqual(timing.vbw_hz, 200000.0)
        self.assertEqual(timing.instrument_sweep_time_s, 0.0601)
        self.assertEqual(timing.quality, MeasurementQuality.EXACT)
        self.assertEqual(timing.deadline_source, "instrument_settings")
        self.assertEqual(timing.warnings, [])
        self.assertEqual(timing.raw_settings["SweepTime"].si_value, 0.0601)
        self.assertIn("AnalyzerSweep/SweepTime", timing.raw_settings["SweepTime"].group_path)

    def test_real_time_extracts_microsecond_sweep_time(self) -> None:
        streams = {
            "Header": _build_header(),
            "Application/FSLSpectrumAnalyzer/Real-Time Spectrum/Current Settings": _build_current_settings(
                measurement="RealTime",
                sweep_time_s=8.192e-05,
                rbw_hz=9375.0,
                vbw_hz=30000.0,
                meas_points=1001,
            ),
            "Application/FSLSpectrumAnalyzer/Real-Time Spectrum/All Traces": _build_all_traces("Real-Time Spectrum"),
        }
        timing = self._parse_with_streams(streams)["Real-Time Spectrum"]
        self.assertEqual(timing.measurement, "RealTime")
        self.assertEqual(timing.point_count, 1001)
        self.assertEqual(timing.instrument_sweep_time_s, 8.192e-05)
        self.assertEqual(timing.rbw_hz, 9375.0)
        self.assertEqual(timing.vbw_hz, 30000.0)
        self.assertIn("RealTime/SweepTime", timing.raw_settings["SweepTime"].group_path)

    def test_unknown_measurement_marks_quality_unknown(self) -> None:
        streams = {
            "Header": _build_header(),
            "Application/FSLSpectrumAnalyzer/Spectrum/Current Settings": _build_current_settings(
                measurement="UnknownMode",
                sweep_time_s=0.01,
                rbw_hz=1000000.0,
                vbw_hz=1000000.0,
            ),
            "Application/FSLSpectrumAnalyzer/Spectrum/All Traces": _build_all_traces(),
        }
        timing = self._parse_with_streams(streams)["Spectrum"]
        self.assertEqual(timing.quality, MeasurementQuality.UNKNOWN)
        self.assertEqual(timing.instrument_sweep_time_s, None)
        self.assertTrue(any(w.code == "mode_specific_group_missing" for w in timing.warnings))

    def test_missing_sweep_time_marks_approximate(self) -> None:
        xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Settings>
  <Root>
    <Prop Name="Measurement" Value="AnalyzerSweep"/>
    <Group Name="AnalyzerSweep">
      <Group Name="ResolutionBandwidth">
        <Prop Name="Value" Value="1000000" UnitId="FREQ_HZ"/>
      </Group>
    </Group>
  </Root>
</Settings>
"""
        streams = {
            "Header": _build_header(),
            "Application/FSLSpectrumAnalyzer/Spectrum/Current Settings": xml,
            "Application/FSLSpectrumAnalyzer/Spectrum/All Traces": _build_all_traces(),
        }
        timing = self._parse_with_streams(streams)["Spectrum"]
        self.assertEqual(timing.quality, MeasurementQuality.APPROXIMATE)
        self.assertTrue(any(w.code == "sweep_time_missing" for w in timing.warnings))

    def test_spectrogram_axis_falls_back_to_matching_trace(self) -> None:
        streams = {
            "Header": _build_header(),
            "Application/FSLSpectrumAnalyzer/Spectrum/Current Settings": _build_current_settings(
                measurement="AnalyzerSweep",
                sweep_time_s=0.0601,
                rbw_hz=200000.0,
                vbw_hz=200000.0,
                meas_points=3,
            ),
            "Application/FSLSpectrumAnalyzer/Spectrum/All Traces": _build_all_traces(),
            "Application/FSLSpectrumAnalyzer/Spectrum/K14Spectrogram/Spectrogram": _build_spectrogram_stream(
                point_count=3, start_hz=0.0, stop_hz=0.0
            ),
        }
        doc = self._parse_doc_with_streams(streams)
        self.assertEqual(len(doc.spectrograms), 1)
        spectrogram = doc.spectrograms[0]
        self.assertEqual(spectrogram.start_hz, 1_000_000_000.0)
        self.assertEqual(spectrogram.stop_hz, 1_100_000_000.0)
        self.assertEqual(spectrogram.metadata.get("axis_source"), "Application/FSLSpectrumAnalyzer/Spectrum/All Traces")


class TimingDeadlineTests(unittest.TestCase):
    def test_deadline_is_min_of_instrument_and_recorded_period(self) -> None:
        timing = AcquisitionTiming(
            instrument_sweep_time_s=0.0601,
            recorded_period_statistics=FramePeriodStatistics(median_s=0.0500),
        )
        deadline = timing.t_deadline_s
        assert deadline is not None
        self.assertAlmostEqual(deadline, 0.0500)

    def test_deadline_ignores_nonpositive_values(self) -> None:
        timing = AcquisitionTiming(instrument_sweep_time_s=-0.01)
        self.assertIsNone(timing.t_deadline_s)
        timing2 = AcquisitionTiming(instrument_sweep_time_s=0.0)
        self.assertIsNone(timing2.t_deadline_s)

    def test_required_point_rate_rejects_zero_deadline(self) -> None:
        timing = AcquisitionTiming(point_count=1001, instrument_sweep_time_s=0.0)
        self.assertIsNone(timing.required_point_rate)

    def test_engineering_target_is_eighty_percent_of_deadline(self) -> None:
        timing = AcquisitionTiming(instrument_sweep_time_s=0.100)
        target = timing.t_target_s
        assert target is not None
        self.assertAlmostEqual(target, 0.080)

    def test_target_is_none_when_deadline_unknown(self) -> None:
        self.assertIsNone(AcquisitionTiming().t_target_s)

    def test_deadline_uses_recorded_when_instrument_missing(self) -> None:
        timing = AcquisitionTiming(recorded_period_statistics=FramePeriodStatistics(median_s=8.192e-05))
        deadline = timing.t_deadline_s
        assert deadline is not None
        self.assertAlmostEqual(deadline, 8.192e-05)


if __name__ == "__main__":
    unittest.main()
