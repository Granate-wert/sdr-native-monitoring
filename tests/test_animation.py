from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.animation import export_waterfall_animation
from esw_dfl.models import SpectrogramInfo, SpectrogramPreview


class AnimationTests(unittest.TestCase):
    def test_selected_fragment_is_exported_to_gif(self) -> None:
        info = SpectrogramInfo(
            key="test",
            title="Test waterfall",
            mode="Real-Time Spectrum",
            measurement="Spectrum",
            measurement_type="Spectrogram",
            source_stream="test/stream",
            line_count=5,
            point_count=8,
            start_hz=1e9,
            stop_hz=1.1e9,
        )
        preview = SpectrogramPreview(
            info=info,
            line_indices=np.arange(5),
            timestamps=np.arange(5, dtype=float) + 1_700_000_000,
            values=np.arange(40, dtype=np.float32).reshape(5, 8) - 60,
        )
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "fragment.gif"
            export_waterfall_animation(preview, output, 1, 3, fps=4, max_frames=10)
            self.assertTrue(output.exists())
            with Image.open(output) as image:
                self.assertEqual(image.n_frames, 3)


if __name__ == "__main__":
    unittest.main()
