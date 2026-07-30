from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from esw_dfl.domain import SourceDescriptor
from esw_dfl.sdr import (
    CalibrationStatus,
    DetectorType,
    DspConfig,
    IqBlock,
    IqRecordingWriter,
    IqReplay,
    InsufficientStorageError,
    PrecisionMode,
    QualityFlag,
    RecordingOptions,
    RecordingQueuePolicy,
    RecordingService,
    SampleFormat,
    SourceType,
    SpectrumFrame,
    SpectrumRecordingWriter,
    SpectrumReplay,
    SpectrumUnit,
    StorageForecast,
    WindowType,
    estimate_storage,
    preflight_storage,
    recover_iq_recording,
    recover_spectrum_recording,
    replay_iq_through_native,
)
from esw_dfl.sdr.native_api import native_availability


class P14RecordingTests(unittest.TestCase):
    def _iq_block(
        self,
        first_sample_index: int = 0,
        sample_count: int = 8,
        *,
        source_sequence: int = 1,
        sample_rate_hz: float = 1_000.0,
        center_frequency_hz: float = 100_000_000.0,
        timestamp_ns: int = 1_000_000_000,
        config_generation: int = 1,
    ) -> IqBlock:
        values = np.arange(sample_count * 2, dtype=np.float32).reshape(sample_count, 2)
        raw = np.asarray(values, dtype="<f4").tobytes()
        return IqBlock(
            source_sequence=source_sequence,
            first_sample_index=first_sample_index,
            timestamp_ns=timestamp_ns,
            center_frequency_hz=center_frequency_hz,
            sample_rate_hz=sample_rate_hz,
            sample_format=SampleFormat.COMPLEX_FLOAT32_LE,
            sample_count=sample_count,
            flags=QualityFlag.NONE,
            samples=np.frombuffer(raw, dtype=np.uint8),
            config_generation=config_generation,
        )

    def _frame(
        self,
        frame_sequence: int = 1,
        *,
        calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED,
        calibration_profile_id: str | None = None,
    ) -> SpectrumFrame:
        size = 16
        source = SourceDescriptor(
            source_type=SourceType.SYNTHETIC,
            source_id="p14-fixture",
            display_name="P14 fixture",
            uri="synthetic:p14",
            metadata={"fixture": True},
            backend_id="cpu",
        )
        return SpectrumFrame(
            source=source,
            frame_sequence=frame_sequence,
            first_sample_index=frame_sequence * 32,
            timestamp_ns=2_000_000_000 + frame_sequence,
            config_generation=3,
            center_frequency_hz=100_000_000.0,
            sample_rate_hz=1_000.0,
            analog_bandwidth_hz=800.0,
            fft_bin_width_hz=62.5,
            enbw_hz=93.75,
            nominal_rbw_hz=93.75,
            fft_size=size,
            hop_size=8,
            window=WindowType.HANN,
            detector=DetectorType.SAMPLE,
            precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
            unit=SpectrumUnit.DBM_BIN if calibration_profile_id else SpectrumUnit.DBFS_BIN,
            frequencies_hz=np.linspace(99_999_500.0, 100_000_500.0, size, dtype=np.float64),
            values=np.linspace(-90.0, -20.0, size, dtype=np.float32),
            calibration_status=calibration_status,
            calibration_profile_id=calibration_profile_id,
            estimated_uncertainty_db=0.25,
            dropped_samples_before=4,
            dropped_iq_blocks_before=1,
            dropped_fft_frames_before=0,
            quality_flags=QualityFlag.CALIBRATION_INTERPOLATED,
        )

    def test_iq_sigmf_roundtrip_and_atomic_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "capture"
            writer = IqRecordingWriter(base)
            writer.start()
            writer.write_block(self._iq_block())
            writer.write_block(self._iq_block(first_sample_index=8, source_sequence=2, timestamp_ns=2_000_000_000))
            meta_path = writer.finalize()
            self.assertTrue(meta_path.exists())
            self.assertFalse(Path(str(base) + ".sigmf-data.part").exists())
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema"], "sdr-native-recording")
            self.assertEqual(metadata["sigmf"]["global"]["core:datatype"], "cf32_le")
            self.assertEqual(metadata["sdr"]["sample_count"], 16)
            blocks = list(IqReplay(base))
            self.assertEqual([item.first_sample_index for item in blocks], [0, 8])
            self.assertEqual(sum(item.sample_count for item in blocks), 16)

    def test_sample_gap_is_explicit_and_replay_does_not_interpolate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "gap"
            writer = IqRecordingWriter(base).start()
            writer.write_block(self._iq_block())
            writer.write_block(self._iq_block(first_sample_index=16, source_sequence=2))
            writer.finalize()
            gaps = [json.loads(line) for line in Path(str(base) + ".sigmf-gaps").read_text().splitlines()]
            self.assertEqual(gaps[0]["reason"], "sample_index_gap")
            self.assertEqual(gaps[0]["sample_count"], 8)
            self.assertEqual([block.first_sample_index for block in IqReplay(base)], [0, 16])

    def test_spectrum_roundtrip_preserves_calibration_and_arrays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "spectrum"
            frame = self._frame(calibration_status=CalibrationStatus.APPLIED, calibration_profile_id="cal-7")
            writer = SpectrumRecordingWriter(base).start()
            writer.write_frame(frame)
            writer.finalize()
            replayed = list(SpectrumReplay(base))
            self.assertEqual(len(replayed), 1)
            restored = replayed[0]
            self.assertEqual(restored.calibration_profile_id, "cal-7")
            self.assertEqual(restored.calibration_status, CalibrationStatus.APPLIED)
            np.testing.assert_array_equal(restored.frequencies_hz, frame.frequencies_hz)
            np.testing.assert_array_equal(restored.values, frame.values)

    def test_service_accounts_overflow_and_writes_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "service"
            service = RecordingService(
                RecordingOptions(
                    output_uri=base,
                    record_iq=True,
                    queue_capacity=1,
                    overflow_policy=RecordingQueuePolicy.DROP_NEWEST,
                )
            )
            writer = service.iq_writer
            assert writer is not None
            original = writer.write_block

            def slow_write(block: IqBlock) -> None:
                time.sleep(0.01)
                original(block)

            writer.write_block = slow_write  # type: ignore[method-assign]
            service.start()
            for index in range(30):
                service.submit_iq(self._iq_block(first_sample_index=index * 8, source_sequence=index + 1))
            stats = service.stop()
            self.assertGreater(stats.dropped_iq_blocks, 0)
            self.assertGreater(stats.gap_count, 0)
            self.assertTrue(Path(str(base) + ".sigmf-meta").exists())

    def test_cancel_leaves_recoverable_part_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "cancelled"
            service = RecordingService(RecordingOptions(output_uri=base, record_iq=True))
            service.start()
            service.submit_iq(self._iq_block())
            stats = service.cancel()
            self.assertTrue(stats.cancelled)
            self.assertTrue(Path(str(base) + ".sigmf-data.part").exists())
            self.assertFalse(Path(str(base) + ".sigmf-meta").exists())

    def test_storage_preflight_and_forecast(self) -> None:
        forecast = estimate_storage(
            sample_rate_hz=1_000.0,
            duration_seconds=10.0,
            sample_format=SampleFormat.COMPLEX_INT16_LE,
            spectrum_frames_per_second=2.0,
            spectrum_bins=256,
            record_iq=True,
            record_spectrum=True,
        )
        self.assertEqual(forecast.iq_bytes_per_second, 4_000)
        self.assertGreater(forecast.estimated_bytes, forecast.iq_bytes_per_second)
        with self.assertRaises(InsufficientStorageError):
            preflight_storage(StorageForecast(0, 0, 10, 5, 0, False))

    def test_iq_corrupt_part_recovery_truncates_and_finalizes_safe_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "recover-iq"
            writer = IqRecordingWriter(base).start()
            writer.write_block(self._iq_block())
            writer.abort("abrupt_cancel")
            data_part = Path(str(base) + ".sigmf-data.part")
            with data_part.open("ab") as handle:
                handle.write(b"x")
            result = recover_iq_recording(base, finalize=True)
            self.assertEqual(result.truncated_bytes, 1)
            self.assertTrue(result.finalized)
            self.assertEqual(len(list(IqReplay(base))), 1)
            self.assertFalse(data_part.exists())

    def test_spectrum_corrupt_part_recovery_drops_bad_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "recover-spectrum"
            writer = SpectrumRecordingWriter(base).start()
            writer.write_frame(self._frame())
            writer.abort("abrupt_cancel")
            with Path(str(base) + ".spectrum.jsonl.part").open("a", encoding="utf-8") as handle:
                handle.write("{broken\n")
            dropped = recover_spectrum_recording(base, finalize=True)
            self.assertEqual(dropped, 1)
            self.assertEqual(len(list(SpectrumReplay(base))), 1)

    def test_config_change_is_captured_in_sigmf_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "config-change"
            writer = IqRecordingWriter(base).start()
            writer.write_block(self._iq_block())
            writer.write_block(
                self._iq_block(
                    first_sample_index=8,
                    sample_rate_hz=2_000.0,
                    center_frequency_hz=101_000_000.0,
                    config_generation=2,
                )
            )
            writer.finalize()
            metadata = json.loads(Path(str(base) + ".sigmf-meta").read_text(encoding="utf-8"))
            captures = metadata["sigmf"]["captures"]
            self.assertEqual(len(captures), 2)
            self.assertEqual(captures[1]["sdr:config_generation"], 2)
            self.assertEqual(captures[1]["core:frequency"], 101_000_000.0)

    def test_large_logical_stream_stays_indexed_and_streamable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "large"
            writer = IqRecordingWriter(base).start()
            for index in range(250):
                writer.write_block(self._iq_block(first_sample_index=index * 8, source_sequence=index + 1))
            writer.finalize()
            replay = IqReplay(base)
            self.assertEqual(sum(1 for _ in replay), 250)
            self.assertEqual(replay.metadata["sdr"]["block_count"], 250)

    @unittest.skipUnless(native_availability().available, "native DSP extension is unavailable")
    def test_native_replay_produces_recorded_iq_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "native-replay"
            writer = IqRecordingWriter(base).start()
            writer.write_block(self._iq_block(sample_count=1_024))
            writer.finalize()
            config = DspConfig(
                fft_size=256,
                hop_size=128,
                window=WindowType.HANN,
                detector=DetectorType.SAMPLE,
                unit=SpectrumUnit.DBFS_BIN,
                precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
            )
            frames = list(replay_iq_through_native(IqReplay(base), config))
            self.assertGreater(len(frames), 0)
            self.assertTrue(all(frame.source.source_type is SourceType.RECORDED_IQ for frame in frames))


if __name__ == "__main__":
    unittest.main()
