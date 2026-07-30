from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import (
    SpectrogramFrameReader,
    SpectrogramIndex,
    SpectrogramRow,
    SpectrogramRowRef,
    load_spectrogram_preview,
    read_spectrogram_frame,
)


class MockRows:
    @staticmethod
    def rows():
        # Intentionally out of file order to verify preview sorts by line_index.
        from esw_dfl.spectrogram import SpectrogramRow
        import numpy as np
        return [
            SpectrogramRow(line_index=2, timestamp=2.0, values=np.array([-10.0, -20.0], dtype=np.float32)),
            SpectrogramRow(line_index=0, timestamp=0.0, values=np.array([-30.0, -40.0], dtype=np.float32)),
            SpectrogramRow(line_index=1, timestamp=1.0, values=np.array([-50.0, -60.0], dtype=np.float32)),
        ]


class SpectrogramRandomAccessTests(unittest.TestCase):
    def test_cached_sector_chain_reads_exact_frame_without_reopening_ole(self) -> None:
        values = np.linspace(-120.0, 5.0, 1001, dtype="<f4")
        payload = base64.b64encode(values.tobytes()).decode("ascii")
        line = (
            '<SgramLine Line="17"><DataBlock Block="0" Data="'
            + payload
            + '"/></SgramLine>'
        ).encode("ascii")
        sector_size = 512
        sector_count = (len(line) + sector_size - 1) // sector_size
        chain = np.arange(sector_count - 1, -1, -1, dtype=np.int32)
        file_bytes = bytearray(sector_size * (sector_count + 1))
        for logical, physical in enumerate(chain):
            chunk = line[logical * sector_size : (logical + 1) * sector_size]
            start = sector_size + int(physical) * sector_size
            file_bytes[start : start + len(chunk)] = chunk

        info = SpectrogramInfo(
            "waterfall", "Waterfall", "RT", "Spectrum", "Spectrogram", "stream",
            1, values.size, 100.0, 300.0,
        )
        index = SpectrogramIndex(
            info,
            np.array([17], dtype=np.int64),
            np.array([0.0], dtype=np.float64),
            np.array([0], dtype=np.int64),
            np.array([len(line)], dtype=np.int32),
            chain,
            sector_size,
        )
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.dfl"
            path.write_bytes(file_bytes)
            with patch("esw_dfl.spectrogram.olefile.OleFileIO", side_effect=AssertionError):
                row = read_spectrogram_frame(path, index, 0)
                reader = SpectrogramFrameReader(path, index)
                reused_row = reader.read_frame(0)
                reader.read_frame(0)
                reader.close()

        self.assertEqual(row.line_index, 17)
        np.testing.assert_array_equal(row.values, values)
        np.testing.assert_array_equal(reused_row.values, values)

    def test_preview_rows_are_sorted_by_source_line_index(self) -> None:
        """Regression: timestamp sorting made the waterfall cursor misaligned."""
        info = SpectrogramInfo(
            "waterfall", "Waterfall", "Spectrum", "Spectrum", "Spectrogram", "stream",
            3, 2, 100.0, 200.0,
        )
        with patch("esw_dfl.spectrogram.iter_spectrogram_rows", return_value=MockRows.rows()):
            preview = load_spectrogram_preview("dummy.dfl", info, max_rows=3)
        np.testing.assert_array_equal(preview.line_indices, np.array([0, 1, 2], dtype=np.int64))
        np.testing.assert_array_almost_equal(
            preview.values,
            np.array([[-30.0, -40.0], [-50.0, -60.0], [-10.0, -20.0]], dtype=np.float32),
        )



    def test_indexed_preview_preserves_short_burst_inside_time_bucket(self) -> None:
        info = SpectrogramInfo(
            "waterfall", "Waterfall", "RT", "Spectrum", "Spectrogram", "stream",
            10, 2, 100.0, 200.0,
        )
        rows = [SpectrogramRow(index, float(index), np.array([-80.0, -80.0], dtype=np.float32)) for index in range(10)]
        rows[4] = SpectrogramRow(4, 4.0, np.array([-12.0, -20.0], dtype=np.float32))
        def streamed_rows(*_args, index_callback=None, **_kwargs):
            if index_callback is not None:
                for row in rows:
                    index_callback(SpectrogramRowRef(row.line_index, row.timestamp, row.line_index, 1))
            return iter(rows)

        with patch("esw_dfl.spectrogram.iter_spectrogram_rows", side_effect=streamed_rows):
            from esw_dfl.spectrogram import load_spectrogram_preview_with_index
            preview, _index = load_spectrogram_preview_with_index("dummy.dfl", info, max_rows=3)
        self.assertEqual(preview.values.shape, (3, 2))
        self.assertEqual(int(preview.line_indices[1]), 3)
        np.testing.assert_array_equal(preview.values[1], np.array([-12.0, -20.0], dtype=np.float32))

if __name__ == "__main__":
    unittest.main()




