"""Sequential native Sweep never guesses the unit of an unlabelled frame."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.domain import SweepConfiguration, SweepExecutionMode, SweepState
from sdr_monitor.services.native_sweep import NativeSweepService, _frame_unit
from tests.test_r10b_native_sweep import _Engine, _Native, _source


class NativeSweepUnitTests(unittest.TestCase):
    def test_only_explicit_supported_units_are_admitted(self):
        for declared, expected in (("DBFS_BIN", "dBFS/bin"), ("dbfs_hz", "dBFS/Hz"),
                                   ("dBFS/bin", "dBFS/bin"), ("dBFS/Hz", "dBFS/Hz")):
            with self.subTest(declared=declared):
                self.assertEqual(_frame_unit(SimpleNamespace(unit=declared)), expected)
                self.assertEqual(_frame_unit(SimpleNamespace(unit=SimpleNamespace(name=declared))), expected)

    def test_missing_unknown_dbm_and_substring_units_are_rejected(self):
        for frame in (SimpleNamespace(), *(SimpleNamespace(unit=value) for value in (
            None, 0, False, "", "volts", "DBM_BIN", "dBm", "NOT_DBFS_BIN", "DBFS_UNKNOWN", object(),
        ))):
            with self.subTest(frame=frame), self.assertRaisesRegex(ValueError, "unit"):
                _frame_unit(frame)

    def test_execution_does_not_publish_complete_or_stitched_data_for_missing_unit(self):
        native = _Native()
        release = []
        service = NativeSweepService(native, _source(), assert_exclusive=lambda: None,
                                     release_lease=lambda: release.append("release"))
        publications = []
        original = _Engine.poll_spectrum_frames

        def missing_unit(engine, count):
            frames = original(engine, count)
            for frame in frames:
                if hasattr(frame, "unit"):
                    del frame.unit
            return frames

        with patch.object(_Engine, "poll_spectrum_frames", missing_unit):
            with self.assertRaisesRegex(ValueError, "unit"):
                service.execute(SweepConfiguration(
                    start_hz=1000, stop_hz=1050, dc_margin_hz=5,
                    settling_s=0.001, dwell_s=0.001,
                    execution_mode=SweepExecutionMode.NATIVE,
                ), publications.append)
        self.assertFalse(any(item.state is SweepState.COMPLETED for item in publications))
        self.assertEqual(release, ["release"])
        self.assertEqual(native.engines[0].disconnect_calls, 1)
