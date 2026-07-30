from __future__ import annotations

import base64
import struct
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.codec import decode_numeric_blocks, decode_scalar_double, decode_timestamp


class CodecTests(unittest.TestCase):
    def test_scalar_comment_is_preferred(self) -> None:
        encoded = base64.b64encode(struct.pack("<d", 123.5)).decode() + " #123.5#"
        self.assertEqual(decode_scalar_double(encoded), 123.5)

    def test_float32_blocks_are_trimmed(self) -> None:
        values = np.arange(8, dtype="<f4")
        encoded = base64.b64encode(values.tobytes()).decode()
        decoded = decode_numeric_blocks([encoded], expected_items=5)
        np.testing.assert_array_equal(decoded, np.arange(5, dtype=np.float32))

    def test_esw_timestamp(self) -> None:
        raw = struct.pack("<4d", 1_783_694_623.0, 0.0, 0.0, 0.25)
        self.assertEqual(
            decode_timestamp(base64.b64encode(raw).decode()), 1_783_694_623.25
        )


if __name__ == "__main__":
    unittest.main()
