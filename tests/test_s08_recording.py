"""S08 bounded live recording tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from sdr_monitor.domain import IQBlock, RecordingOptions, RecordingState, SpectrumFrame
from sdr_monitor.services.recording_session import RecordingService


class S08RecordingTests(unittest.TestCase):
    def test_iq_only_writes_exact_blocks_and_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "iq.sdrrec"
            service = RecordingService()
            service.start(RecordingOptions(str(path), record_iq=True, record_spectrum=False, queue_capacity=8))
            for sequence in range(5):
                self.assertTrue(service.submit_iq(IQBlock(sequence, sequence + 1, np.ones(16, dtype=np.complex64), 1e6)))
            result = service.stop()
            self.assertEqual(result.state, RecordingState.COMPLETED)
            self.assertEqual(result.iq_blocks, 5)
            self.assertEqual(result.spectrum_frames, 0)
            self.assertTrue(path.exists())
            self.assertFalse(Path(str(path) + ".part").exists())
            service.close()

    def test_spectrum_only_is_non_empty_and_combined_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "spectrum.sdrrec"
            service = RecordingService()
            service.start(RecordingOptions(str(path), record_iq=False, record_spectrum=True, metadata={"config_generation": 7}))
            self.assertFalse(service.submit_iq(IQBlock(0, 1, np.ones(4, dtype=np.complex64), 1e6)))
            self.assertTrue(service.submit_spectrum(SpectrumFrame(0, 100, np.arange(4), np.arange(4), "dBFS/bin")))
            result = service.stop()
            self.assertEqual(result.spectrum_frames, 1)
            self.assertGreater(path.stat().st_size, 100)
            self.assertEqual(result.metadata["config_generation"], 7)

    def test_source_bus_fans_out_without_synthetic_producer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = RecordingService()
            service.start(RecordingOptions(str(Path(temporary) / "bus.sdrrec"), queue_capacity=4))
            self.assertEqual(service.source_bus.publish_iq(IQBlock(1, 1, np.ones(2, dtype=np.complex64), 1e6)), 1)
            service.source_bus.publish_metadata({"event": "config"})
            result = service.stop()
            self.assertEqual(result.iq_blocks, 1)

    def test_slow_writer_exposes_drops_and_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = RecordingService()
            service.start(RecordingOptions(str(Path(temporary) / "slow.sdrrec"), queue_capacity=1))
            original = service._write_bytes
            def slow(data: bytes) -> None:
                time.sleep(0.02)
                original(data)
            service._write_bytes = slow  # type: ignore[method-assign]
            for sequence in range(50):
                service.submit_iq(IQBlock(sequence, sequence, np.ones(4, dtype=np.complex64), 1e6))
            result = service.stop(timeout_s=4)
            self.assertGreater(result.drops, 0)
            self.assertEqual(result.drops, result.gaps)

    def test_writer_error_keeps_partial_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "disk.sdrrec"
            service = RecordingService()
            service.start(RecordingOptions(str(path), queue_capacity=4))
            original = service._write_bytes
            def fail_after_header(data: bytes) -> None:
                if data.startswith(b"{"):
                    original(data)
                    return
                raise OSError("simulated disk full")
            service._write_bytes = fail_after_header  # type: ignore[method-assign]
            service.submit_iq(IQBlock(0, 1, np.ones(4, dtype=np.complex64), 1e6))
            time.sleep(0.05)
            result = service.stop()
            self.assertEqual(result.state, RecordingState.FAILED)
            self.assertTrue(Path(str(path) + ".part").exists())
            self.assertTrue(service.recover_partial(Path(str(path) + ".part"))["recovered"])


if __name__ == "__main__":
    unittest.main()
