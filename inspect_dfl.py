"""Development helper: inspect streams and strings in an R&S DFL container."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import olefile


ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


def strings(data: bytes, limit: int = 200) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for match in ASCII_RE.finditer(data):
        found.append((match.start(), match.group().decode("ascii", "replace")))
    for match in UTF16_RE.finditer(data):
        found.append((match.start(), match.group().decode("utf-16le", "replace")))
    found.sort(key=lambda item: item[0])
    return found[:limit]


def inspect(path: Path, sample_bytes: int, max_strings: int) -> None:
    print(f"FILE {path} ({path.stat().st_size:,} bytes)")
    with olefile.OleFileIO(path) as ole:
        for parts in ole.listdir():
            name = "/".join(parts)
            size = ole.get_size(parts)
            stream = ole.openstream(parts)
            head = stream.read(min(size, sample_bytes))
            tail = b""
            if size > sample_bytes:
                stream.seek(max(0, size - sample_bytes))
                tail = stream.read(sample_bytes)
            print(f"\nSTREAM {name} ({size:,} bytes)")
            for offset, value in strings(head, max_strings):
                print(f"  H+0x{offset:08X}: {value}")
            if tail:
                for offset, value in strings(tail, max_strings):
                    print(f"  T+0x{size - sample_bytes + offset:08X}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--sample-bytes", type=int, default=2_000_000)
    parser.add_argument("--max-strings", type=int, default=200)
    args = parser.parse_args()
    for path in args.paths:
        inspect(path, args.sample_bytes, args.max_strings)


if __name__ == "__main__":
    main()
