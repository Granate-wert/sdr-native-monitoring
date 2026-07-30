"""Tests for SpectrogramFrameReader.iter_frames (review P1 / HMP-PERSIST-004).

Covers: iterator values identical to read_frame, stop-after-cancel with the
handle closed, and the read-only handle policy.
"""

from __future__ import annotations

import base64
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import (
    OperationCancelled,
    SpectrogramFrameReader,
    SpectrogramIndex,
    _decode_line,
    _decode_line_python,
    native_decoder_available,
)


FREQ_BINS = 8


def _write_fake_dfl(root: Path, frames: list[np.ndarray]) -> tuple[Path, SpectrogramIndex]:
    """Build a fake CFB-less file readable by SpectrogramFrameReader via a sector chain."""
    sector_size = 512
    stream = bytearray()
    offsets: list[int] = []
    lengths: list[int] = []
    for index, values in enumerate(frames):
        payload = base64.b64encode(np.ascontiguousarray(values, dtype="<f4").tobytes()).decode("ascii")
        line = (
            f'<SgramLine Line="{index}"><DataBlock Block="0" Data="' + payload + '"/></SgramLine>'
        ).encode("ascii")
        offsets.append(len(stream))
        lengths.append(len(line))
        stream += line
    sector_count = (len(stream) + sector_size - 1) // sector_size
    stream += b"\x00" * (sector_count * sector_size - len(stream))
    path = root / "fake.dfl"
    path.write_bytes(b"\x00" * sector_size + bytes(stream))
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=len(frames),
        point_count=int(frames[0].size),
        start_hz=100.0,
        stop_hz=800.0,
    )
    index = SpectrogramIndex(
        info=info,
        line_indices=np.arange(len(frames), dtype=np.int64),
        timestamps=np.arange(len(frames), dtype=np.float64),
        offsets=np.asarray(offsets, dtype=np.int64),
        lengths=np.asarray(lengths, dtype=np.int32),
        sector_chain=np.arange(sector_count, dtype=np.int32),
        sector_size=sector_size,
    )
    return path, index


def _frames() -> list[np.ndarray]:
    return [np.full(FREQ_BINS, -80.0 + index, dtype=np.float32) for index in range(8)]


class IterFramesTests(unittest.TestCase):
    def test_native_decoder_matches_python_for_timestamp_and_multiblock_line(self) -> None:
        values = np.linspace(-95.5, 12.25, FREQ_BINS, dtype="<f4")
        raw = values.tobytes()
        split = 4 * (values.size // 2)
        first = base64.b64encode(raw[:split]).decode("ascii")
        second = base64.b64encode(raw[split:]).decode("ascii")
        timestamp = base64.b64encode(struct.pack('<dddd', 1_720_000_000.0, 0.0, 0.0, 0.125)).decode('ascii')
        blob = (
            '<SgramLine Line="41" Timestamp="' + timestamp + '">'
            f'<DataBlock Block="1" Data="{second}"/>'
            f'<DataBlock Block="0" Data="{first}"/>'
            '</SgramLine>'
        ).encode("ascii")

        expected = _decode_line_python(blob, FREQ_BINS)
        actual = _decode_line(blob, FREQ_BINS)
        self.assertIsNotNone(expected)
        self.assertIsNotNone(actual)
        assert expected is not None and actual is not None
        self.assertEqual(actual.line_index, expected.line_index)
        self.assertEqual(actual.timestamp, expected.timestamp)
        np.testing.assert_array_equal(actual.values, expected.values)

    @unittest.skipUnless(native_decoder_available(), "optional Rust decoder is not built")
    def test_native_decoder_values_keep_immutable_buffer_without_float_copy(self) -> None:
        values = np.linspace(-90.0, -10.0, FREQ_BINS, dtype="<f4")
        payload = base64.b64encode(values.tobytes()).decode("ascii")
        blob = (
            '<SgramLine Line="7"><DataBlock Block="0" Data="'
            + payload
            + '"/></SgramLine>'
        ).encode("ascii")
        row = _decode_line(blob, FREQ_BINS)
        self.assertIsNotNone(row)
        assert row is not None
        np.testing.assert_array_equal(row.values, values)
        self.assertFalse(row.values.flags.owndata)
        self.assertFalse(row.values.flags.writeable)

    @unittest.skipUnless(native_decoder_available(), "optional Rust decoder is not built")
    def test_native_decoder_is_loaded_when_built(self) -> None:
        self.assertTrue(native_decoder_available())
    def test_iter_frames_matches_read_frame(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path, index = _write_fake_dfl(Path(root), _frames())
            expected = [
                SpectrogramFrameReader(path, index).read_frame(frame).values
                for frame in range(index.frame_count)
            ]
            iterator = SpectrogramFrameReader(path, index).iter_frames(range(index.frame_count))
            actual = [row.values for row in iterator]
            self.assertEqual(len(actual), len(expected))
            for actual_row, expected_row in zip(actual, expected):
                np.testing.assert_array_equal(actual_row, expected_row)

    def test_iter_frames_stops_after_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path, index = _write_fake_dfl(Path(root), _frames())
            reader = SpectrogramFrameReader(path, index)
            cancel = threading.Event()
            received = []
            with self.assertRaises(OperationCancelled):
                for row in reader.iter_frames(range(index.frame_count), cancel=cancel):
                    received.append(row.line_index)
                    if len(received) == 3:
                        cancel.set()
            # The cancellation lands after the current frame: 3 rows yielded,
            # no read for the 5th frame (index 4) happened.
            self.assertEqual(len(received), 3)
            self.assertIsNone(reader._source, "iterator must close the read-only handle")

    def test_iterator_handle_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path, index = _write_fake_dfl(Path(root), _frames())
            reader = SpectrogramFrameReader(path, index)
            generator = reader.iter_frames(range(2))
            next(generator)
            self.assertIsNotNone(reader._source)
            assert reader._source is not None
            self.assertFalse(reader._source.writable())
            generator.close()
            self.assertIsNone(reader._source)


if __name__ == "__main__":
    unittest.main()
