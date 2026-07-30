from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl import DflParser
from esw_dfl.spectrogram import load_spectrogram_preview


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--rows", type=int, default=25)
    args = parser.parse_args()
    reader = DflParser()
    for path in args.files:
        document = reader.parse(path)
        print(path)
        print(f"  traces={len(document.traces)}, spectrograms={len(document.spectrograms)}")
        for trace in document.traces:
            assert trace.x.size == trace.y.size > 0
            print(f"  {trace.title}: {trace.y.size} points")
        for info in document.spectrograms:
            preview = load_spectrogram_preview(path, info, args.rows)
            assert preview.values.shape[1] == info.point_count
            print(f"  {info.title}: preview={preview.values.shape}")


if __name__ == "__main__":
    main()
