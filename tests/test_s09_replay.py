"""S09 indexed replay and async reprocess tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdr_monitor.domain import IQBlock, RecordingOptions, ReplayKind, ReplayState, SpectrumFrame
from sdr_monitor.services.recording_session import RecordingService
from sdr_monitor.services.replay_session import ReplayService, RecordingReader


class S09ReplayTests(unittest.TestCase):
    def _record(self, root: Path, blocks: int = 4) -> Path:
        path = root / "sample.sdrrec"
        service = RecordingService()
        service.start(RecordingOptions(str(path), record_iq=True, record_spectrum=True, queue_capacity=32))
        for sequence in range(blocks):
            service.submit_iq(IQBlock(sequence, sequence * 100, np.ones(8, dtype=np.complex64) * (sequence + 1), 1e6))
            service.submit_spectrum(SpectrumFrame(sequence, sequence * 100, np.arange(4), np.arange(4) + sequence))
        result = service.stop()
        self.assertEqual(result.state, ReplayState.COMPLETED)
        return path

    def test_index_contains_byte_offsets_and_sparse_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._record(Path(temporary))
            reader = RecordingReader(path)
            self.assertEqual(reader.index.frame_count, 8)
            self.assertTrue(all(entry.offset > 0 and entry.size > 4 for entry in reader.index.entries))
            self.assertEqual(len(reader.index.entries_for(ReplayKind.IQ)), 4)
            reader.close()

    def test_seek_physically_changes_next_frame_and_pause_preserves_position(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._record(Path(temporary))
            replay = ReplayService()
            replay.open(path, kind=ReplayKind.IQ)
            first = replay.read_next()
            self.assertEqual(first.sequence, 0)
            replay.seek(0.75)
            position = replay.position
            next_frame = replay.read_next()
            self.assertEqual(next_frame.sequence, 3)
            replay.pause()
            self.assertEqual(replay.position.ordinal, position.ordinal + 1)
            replay.close()

    def test_play_speed_and_shared_frame_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._record(Path(temporary))
            replay = ReplayService()
            received = []
            replay.frame_bus.subscribe(received.append)
            replay.open(path, kind=ReplayKind.SPECTRUM)
            replay.set_speed(8.0)
            replay.play()
            while replay.state is ReplayState.PLAYING:
                replay.tick()
            self.assertEqual(len(received), 4)
            self.assertEqual(received[-1].sequence, 3)
            with self.assertRaises(ValueError):
                replay.set_speed(0.1)
            replay.close()

    def test_async_reprocess_and_cuda_unavailable_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._record(Path(temporary), blocks=5)
            replay = ReplayService()
            result = replay.reprocess_iq(path, "cuda").result(timeout=5)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.backend_used, "cpu")
            self.assertIn("CUDA unavailable", result.warning)
            self.assertEqual(result.frames_processed, 5)
            self.assertTrue(Path(result.output_path).exists())
            replay.close()

    def test_reprocess_cancel_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._record(Path(temporary), blocks=200)
            replay = ReplayService()
            future = replay.reprocess_iq(path, "cpu")
            replay.cancel_reprocess()
            result = future.result(timeout=5)
            self.assertIn(result.status, ("cancelled", "completed"))
            replay.close()


if __name__ == "__main__":
    unittest.main()
