from __future__ import annotations

import base64
import threading
from dataclasses import dataclass
from io import SEEK_CUR, SEEK_END, SEEK_SET
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Iterator

import numpy as np
import olefile

from .codec import decode_timestamp, parse_tag_attributes
from .models import FramePeriodStatistics, SpectrogramInfo, SpectrogramPreview

try:
    # Optional Rust/PyO3 fast path. It is intentionally not a requirement for
    # source checkouts: all decoding semantics remain available in Python.
    from ._sgram_native import decode_sgram_line as _native_decode_sgram_line
except ImportError:
    _native_decode_sgram_line = None



ProgressCallback = Callable[[float, str], None]
LINE_OPEN = b"<SgramLine"
LINE_CLOSE = b"</SgramLine>"


class OperationCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class SpectrogramRow:
    line_index: int
    timestamp: float
    values: np.ndarray


@dataclass(frozen=True, slots=True)
class SpectrogramRowRef:
    line_index: int
    timestamp: float
    offset: int
    length: int


@dataclass(slots=True)
class SpectrogramIndex:
    """Compact time-ordered random-access index for one XML spectrogram stream."""

    info: SpectrogramInfo
    line_indices: np.ndarray
    timestamps: np.ndarray
    offsets: np.ndarray
    lengths: np.ndarray
    sector_chain: np.ndarray | None = None
    sector_size: int | None = None

    @property
    def frame_count(self) -> int:
        return int(self.offsets.size)

    @classmethod
    def from_refs(
        cls,
        info: SpectrogramInfo,
        refs: list[SpectrogramRowRef],
        sector_chain: np.ndarray | None = None,
        sector_size: int | None = None,
    ) -> "SpectrogramIndex":
        if not refs:
            return cls(
                info,
                np.empty(0, dtype=np.int64),
                np.empty(0, dtype=np.float64),
                np.empty(0, dtype=np.int64),
                np.empty(0, dtype=np.int32),
                sector_chain,
                sector_size,
            )
        timestamps = np.asarray([ref.timestamp for ref in refs], dtype=np.float64)
        file_order = np.arange(len(refs), dtype=np.int64)
        finite_key = np.where(np.isfinite(timestamps), timestamps, np.inf)
        order = np.lexsort((file_order, finite_key))
        return cls(
            info,
            np.asarray([refs[index].line_index for index in order], dtype=np.int64),
            timestamps[order],
            np.asarray([refs[index].offset for index in order], dtype=np.int64),
            np.asarray([refs[index].length for index in order], dtype=np.int32),
            sector_chain,
            sector_size,
        )


def compute_frame_period_statistics(index: SpectrogramIndex) -> FramePeriodStatistics:
    """Compute positive timestamp-delta statistics from a SpectrogramIndex.

    Uses only the compact index, never the full value matrix.
    """
    timestamps = index.timestamps
    if timestamps.size < 2:
        return FramePeriodStatistics()
    finite = timestamps[np.isfinite(timestamps)]
    if finite.size < 2:
        return FramePeriodStatistics()
    deltas = np.diff(finite)
    positive = deltas[deltas > 0]
    if positive.size == 0:
        return FramePeriodStatistics(count=int(finite.size))
    return FramePeriodStatistics(
        count=int(positive.size),
        min_s=float(np.min(positive)),
        median_s=float(np.median(positive)),
        mean_s=float(np.mean(positive)),
        p95_s=float(np.percentile(positive, 95)),
        p99_s=float(np.percentile(positive, 99)),
        max_s=float(np.max(positive)),
    )


class OleSectorStream:
    """Read a regular CFB stream on demand instead of olefile's full BytesIO copy."""

    def __init__(
        self,
        ole: olefile.OleFileIO,
        size: int,
        chain: np.ndarray,
    ) -> None:
        self.ole = ole
        self.size = int(size)
        self.chain = np.asarray(chain, dtype=np.int32)
        self.position = 0
        self.sector_size = int(ole.sectorsize)

    def seek(self, offset: int, whence: int = SEEK_SET) -> int:
        if whence == SEEK_SET:
            position = offset
        elif whence == SEEK_CUR:
            position = self.position + offset
        elif whence == SEEK_END:
            position = self.size + offset
        else:
            raise ValueError(f"Неподдерживаемый whence: {whence}")
        self.position = int(np.clip(position, 0, self.size))
        return self.position

    def tell(self) -> int:
        return self.position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.size - self.position
        remaining = min(int(size), self.size - self.position)
        if remaining <= 0:
            return b""
        parts: list[bytes] = []
        while remaining > 0:
            logical_sector, within = divmod(self.position, self.sector_size)
            if logical_sector >= self.chain.size:
                break
            physical_sector = int(self.chain[logical_sector])
            if within:
                count = min(remaining, self.sector_size - within)
                self.ole.fp.seek(self.sector_size + physical_sector * self.sector_size + within)
                data = self.ole.fp.read(count)
            else:
                maximum_sectors = min(
                    self.chain.size - logical_sector,
                    max(1, remaining // self.sector_size),
                )
                run = 1
                while (
                    run < maximum_sectors
                    and int(self.chain[logical_sector + run]) == physical_sector + run
                ):
                    run += 1
                count = min(remaining, run * self.sector_size)
                self.ole.fp.seek(self.sector_size + physical_sector * self.sector_size)
                data = self.ole.fp.read(count)
            if not data:
                break
            parts.append(data)
            consumed = len(data)
            self.position += consumed
            remaining -= consumed
        return b"".join(parts)


def _stream_sector_chain(
    ole: olefile.OleFileIO, source_stream: str
) -> tuple[int, np.ndarray] | None:
    sid = ole._find(source_stream)  # noqa: SLF001 - olefile exposes no lazy stream API
    entry = ole.direntries[sid]
    if entry.size < ole.minisectorcutoff:
        return None
    sector_count = (entry.size + ole.sectorsize - 1) // ole.sectorsize
    chain = np.empty(sector_count, dtype=np.int32)
    sector = int(entry.isectStart)
    for index in range(sector_count):
        if sector < 0 or sector >= len(ole.fat):
            raise OSError("Повреждённая FAT-цепочка потока spectrogram")
        chain[index] = sector
        sector = int(ole.fat[sector]) & 0xFFFFFFFF
    return int(entry.size), chain


def _open_lazy_stream(
    ole: olefile.OleFileIO,
    source_stream: str,
    sector_chain: np.ndarray | None = None,
) -> tuple[Any, np.ndarray | None]:
    if sector_chain is not None:
        sid = ole._find(source_stream)  # noqa: SLF001
        entry = ole.direntries[sid]
        if entry.size >= ole.minisectorcutoff:
            return OleSectorStream(ole, int(entry.size), sector_chain), sector_chain
    chain_info = _stream_sector_chain(ole, source_stream)
    if chain_info is None:
        return ole.openstream(source_stream), None
    size, discovered_chain = chain_info
    chain = sector_chain if sector_chain is not None else discovered_chain
    return OleSectorStream(ole, size, chain), chain


def sample_line_indices(line_count: int, max_rows: int) -> set[int]:
    if line_count <= 0:
        return set()
    count = min(line_count, max(1, max_rows))
    return {int(index) for index in np.linspace(0, line_count - 1, count, dtype=np.int64)}


class _TemporalPeakPreview:
    """Bounded, peak-preserving temporal reduction for the waterfall display."""

    def __init__(self, info: SpectrogramInfo, max_rows: int) -> None:
        self.info = info
        self.row_count = min(max(1, int(max_rows)), max(0, int(info.line_count)))
        self.values = np.full((self.row_count, info.point_count), -np.inf, dtype=np.float32)
        self.timestamps = np.full(self.row_count, np.nan, dtype=np.float64)
        self.seen = np.zeros(self.row_count, dtype=bool)

    def add(self, row: SpectrogramRow, source_frame_index: int) -> None:
        if self.row_count == 0 or source_frame_index < 0:
            return
        bucket = min(
            self.row_count - 1,
            max(0, int(source_frame_index) * self.row_count // max(1, self.info.line_count)),
        )
        width = min(self.values.shape[1], row.values.size)
        if width:
            np.maximum(self.values[bucket, :width], row.values[:width], out=self.values[bucket, :width])
        if not self.seen[bucket] and np.isfinite(row.timestamp):
            self.timestamps[bucket] = row.timestamp
        self.seen[bucket] = True

    def build(self) -> SpectrogramPreview:
        if self.row_count == 0 or not np.any(self.seen):
            return SpectrogramPreview(
                info=self.info,
                line_indices=np.empty(0, dtype=np.int64),
                timestamps=np.empty(0, dtype=np.float64),
                values=np.empty((0, self.info.point_count), dtype=np.float32),
            )
        buckets = np.flatnonzero(self.seen)
        starts = (buckets * self.info.line_count // self.row_count).astype(np.int64, copy=False)
        values = self.values[buckets].copy()
        values[~np.isfinite(values)] = np.nan
        return SpectrogramPreview(
            info=self.info,
            line_indices=starts,
            timestamps=self.timestamps[buckets],
            values=values,
        )

def _tag_attribute_bytes(tag: bytes, name: bytes) -> bytes | None:
    """Extract one quoted XML attribute without allocating a decoded dict."""
    marker = name + b'="'
    start = tag.find(marker)
    if start < 0:
        return None
    value_start = start + len(marker)
    value_end = tag.find(b'"', value_start)
    return tag[value_start:value_end] if value_end >= value_start else None


def _decode_line_python(blob: bytes, point_count: int) -> SpectrogramRow | None:
    """Decode a compact SgramLine with a bytes-only fast XML path.

    ESW frame XML is deliberately parsed only as far as the Line, Timestamp,
    Block and Data attributes. This avoids a dict plus UTF-8 allocation per
    attribute while retaining multi-block ordering and the generic fallback
    semantics of the original decoder.
    """
    tag_end = blob.find(b">")
    if tag_end < 0:
        return None
    header = blob[: tag_end + 1]
    line_raw = _tag_attribute_bytes(header, b"Line")
    try:
        line_index = int(line_raw or b"0")
    except ValueError:
        return None
    timestamp_raw = _tag_attribute_bytes(header, b"Timestamp")
    timestamp = decode_timestamp(timestamp_raw.decode("ascii", "replace") if timestamp_raw else None)

    blocks: list[tuple[int, bytes]] = []
    position = tag_end + 1
    while True:
        start = blob.find(b"<DataBlock", position)
        if start < 0:
            break
        end = blob.find(b"/>", start)
        if end < 0:
            break
        tag = blob[start : end + 2]
        try:
            block_raw = _tag_attribute_bytes(tag, b"Block")
            block_index = int(block_raw or b"0")
            payload = _tag_attribute_bytes(tag, b"Data")
            if payload:
                blocks.append((block_index, base64.b64decode(payload, validate=False)))
        except (ValueError, TypeError):
            pass
        position = end + 2
    if not blocks:
        raw = b""
    elif len(blocks) == 1:
        raw = blocks[0][1]
    else:
        raw = b"".join(payload for _, payload in sorted(blocks))
    values = np.frombuffer(raw, dtype="<f4")
    if point_count > 0:
        values = values[:point_count]
    if timestamp is None:
        timestamp = float("nan")
    return SpectrogramRow(line_index, timestamp, values.copy())


def native_decoder_available() -> bool:
    """Whether the optional compiled SgramLine decoder was imported."""

    return _native_decode_sgram_line is not None


def _native_decoded_row(decoded: Any, point_count: int) -> SpectrogramRow:
    line_index, timestamp_raw, raw = decoded
    timestamp: float | None
    if isinstance(timestamp_raw, (int, float)):
        timestamp = float(timestamp_raw)
    else:
        timestamp = decode_timestamp(
            bytes(timestamp_raw).decode("ascii", "replace")
            if timestamp_raw is not None
            else None
        )
    # np.frombuffer retains ``raw`` as its immutable owner. No float32 copy is
    # needed; all current SpectrogramRow consumers are read-only.
    values = np.frombuffer(raw, dtype="<f4")
    if point_count > 0:
        values = values[:point_count]
    return SpectrogramRow(
        int(line_index),
        float("nan") if timestamp is None else timestamp,
        values,
    )


def _decode_line(blob: bytes, point_count: int) -> SpectrogramRow | None:
    """Decode a frame, preferring Rust while retaining a semantic fallback."""
    if _native_decode_sgram_line is not None:
        try:
            decoded = _native_decode_sgram_line(blob, max(0, point_count))
            if decoded is not None:
                return _native_decoded_row(decoded, point_count)
        except (TypeError, ValueError):
            # Python remains the authoritative compatibility implementation for
            # malformed vendor fragments and ABI-mismatched optional modules.
            pass
    return _decode_line_python(blob, point_count)


def iter_spectrogram_rows(
    path: str | Path,
    info: SpectrogramInfo,
    selected_lines: set[int] | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    chunk_size: int = 8 * 1024 * 1024,
    index_callback: Callable[[SpectrogramRowRef], None] | None = None,
    sector_chain_callback: Callable[[np.ndarray], None] | None = None,
) -> Iterator[SpectrogramRow]:
    """Stream spectrogram rows without materializing the 600+ MB XML document."""
    with olefile.OleFileIO(Path(path)) as ole:
        stream, sector_chain = _open_lazy_stream(ole, info.source_stream)
        if sector_chain_callback is not None and sector_chain is not None:
            sector_chain_callback(sector_chain)
        total = ole.get_size(info.source_stream)
        read_bytes = 0
        buffer = b""
        buffer_offset = 0
        last_percent = -1
        while True:
            if cancel is not None and cancel.is_set():
                raise OperationCancelled("Операция отменена")
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            chunk_offset = read_bytes
            read_bytes += len(chunk)
            data = buffer + chunk
            data_offset = buffer_offset if buffer else chunk_offset
            cursor = 0
            incomplete_start = -1
            while True:
                start = data.find(LINE_OPEN, cursor)
                if start < 0:
                    break
                tag_end = data.find(b">", start)
                if tag_end < 0:
                    incomplete_start = start
                    break
                end = data.find(LINE_CLOSE, tag_end)
                if end < 0:
                    incomplete_start = start
                    break
                end += len(LINE_CLOSE)
                attrs = parse_tag_attributes(data[start : tag_end + 1])
                try:
                    line_index = int(attrs.get("Line", "-1") or -1)
                except ValueError:
                    line_index = -1
                timestamp = decode_timestamp(attrs.get("Timestamp"))
                if index_callback is not None:
                    index_callback(
                        SpectrogramRowRef(
                            line_index=line_index,
                            timestamp=float(timestamp) if timestamp is not None else float("nan"),
                            offset=data_offset + start,
                            length=end - start,
                        )
                    )
                if selected_lines is None or line_index in selected_lines:
                    blob = data[start:end]
                    row = _decode_line(blob, info.point_count)
                    if row is not None:
                        yield row
                cursor = end
            if incomplete_start >= 0:
                buffer = data[incomplete_start:]
                buffer_offset = data_offset + incomplete_start
            else:
                keep_from = max(cursor, len(data) - 64)
                buffer = data[keep_from:]
                buffer_offset = data_offset + keep_from
            percent = int(read_bytes * 100 / max(1, total))
            if progress is not None and percent != last_percent:
                last_percent = percent
                progress(percent / 100.0, f"Чтение спектрограммы: {percent}%")
        if progress is not None:
            progress(1.0, "Спектрограмма прочитана")


def load_spectrogram_preview(
    path: str | Path,
    info: SpectrogramInfo,
    max_rows: int = 800,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> SpectrogramPreview:
    selected = sample_line_indices(info.line_count, max_rows)
    rows = list(iter_spectrogram_rows(path, info, selected, progress, cancel))
    rows.sort(key=lambda row: row.line_index)
    if not rows:
        return SpectrogramPreview(
            info=info,
            line_indices=np.empty(0, dtype=np.int64),
            timestamps=np.empty(0, dtype=np.float64),
            values=np.empty((0, info.point_count), dtype=np.float32),
        )
    widths = {row.values.size for row in rows}
    if len(widths) != 1:
        width = min(widths)
        values = np.vstack([row.values[:width] for row in rows])
    else:
        values = np.vstack([row.values for row in rows])
    return SpectrogramPreview(
        info=info,
        line_indices=np.asarray([row.line_index for row in rows], dtype=np.int64),
        timestamps=np.asarray([row.timestamp for row in rows], dtype=np.float64),
        values=values,
    )


def load_spectrogram_preview_with_index(
    path: str | Path,
    info: SpectrogramInfo,
    max_rows: int = 800,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> tuple[SpectrogramPreview, SpectrogramIndex]:
    """Build a logical-playback index and bounded peak-preserving preview.

    The DFL stream may be stored out of timestamp order.  The first streaming
    pass therefore builds the compact index without decoding sample arrays; the
    second pass maps each row into the same timestamp-ordered frame coordinate
    used by playback, retaining only a per-bucket frequency maximum.
    """
    refs: list[SpectrogramRowRef] = []
    sector_chains: list[np.ndarray] = []
    for _row in iter_spectrogram_rows(
        path,
        info,
        set(),
        progress,
        cancel,
        index_callback=refs.append,
        sector_chain_callback=sector_chains.append,
    ):
        pass
    sector_chain = sector_chains[0] if sector_chains else None
    sector_size = _cfb_sector_size(path) if sector_chain is not None else None
    index = SpectrogramIndex.from_refs(info, refs, sector_chain, sector_size)
    ordered_frame_by_line = {
        int(line): frame for frame, line in enumerate(index.line_indices.tolist())
    }
    preview_builder = _TemporalPeakPreview(info, max_rows)
    for row in iter_spectrogram_rows(path, info, None, progress, cancel):
        frame = ordered_frame_by_line.get(int(row.line_index))
        if frame is not None:
            preview_builder.add(row, frame)
    return preview_builder.build(), index

def _cfb_sector_size(path: str | Path) -> int:
    """Read the CFB sector size from the fixed header without parsing its FAT."""
    with Path(path).open("rb") as source:
        header = source.read(32)
    if len(header) < 32 or header[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise OSError("Файл не является CFB-контейнером")
    exponent = int.from_bytes(header[30:32], "little")
    sector_size = 1 << exponent
    if sector_size not in (512, 4096):
        raise OSError(f"Неподдерживаемый размер сектора CFB: {sector_size}")
    return sector_size


def _read_regular_sector_blob(
    path: str | Path,
    chain: np.ndarray,
    sector_size: int,
    offset: int,
    length: int,
) -> bytes:
    """Read a logical regular-stream slice directly from its cached FAT chain."""
    position = int(offset)
    remaining = int(length)
    parts: list[bytes] = []
    with Path(path).open("rb") as source:
        while remaining > 0:
            logical_sector, within = divmod(position, sector_size)
            if logical_sector >= chain.size:
                break
            physical_sector = int(chain[logical_sector])
            count = min(remaining, sector_size - within)
            source.seek(sector_size + physical_sector * sector_size + within)
            data = source.read(count)
            if not data:
                break
            parts.append(data)
            consumed = len(data)
            position += consumed
            remaining -= consumed
    blob = b"".join(parts)
    if len(blob) != length:
        raise OSError(
            f"Неполное чтение spectrogram: ожидалось {length}, получено {len(blob)} байт"
        )
    return blob


class SpectrogramFrameReader:
    """Thread-safe reusable exact-frame reader with one read-only file handle."""

    def __init__(self, path: str | Path, index: SpectrogramIndex) -> None:
        self.path = Path(path)
        self.index = index
        self._lock = threading.Lock()
        self._source: Any | None = None

    def frame_payload_size(self, frame_index: int) -> int:
        """Return indexed encoded row bytes without touching the source DFL."""
        if not 0 <= frame_index < self.index.frame_count:
            raise IndexError(frame_index)
        return int(self.index.lengths[frame_index])

    def read_frame(self, frame_index: int) -> SpectrogramRow:
        if not 0 <= frame_index < self.index.frame_count:
            raise IndexError(frame_index)
        if self.index.sector_chain is None or self.index.sector_size is None:
            return read_spectrogram_frame(self.path, self.index, frame_index)
        offset = int(self.index.offsets[frame_index])
        length = int(self.index.lengths[frame_index])
        with self._lock:
            if self._source is None:
                self._source = self.path.open("rb")
            blob = _read_regular_sector_blob_from_handle(
                self._source,
                self.index.sector_chain,
                self.index.sector_size,
                offset,
                length,
            )
        row = _decode_line(blob, self.index.info.point_count)
        if row is None:
            raise ValueError(f"Не удалось декодировать кадр {frame_index}")
        return row

    def iter_frames(
        self,
        frame_indices: Iterable[int],
        cancel: threading.Event | None = None,
    ) -> Generator[SpectrogramRow, None, None]:
        """Yield frames one by one with a cancellation check around every read.

        Read-only: the underlying handle is opened ``"rb"`` exactly like
        :meth:`read_frame`. The cancel event is checked immediately before
        every blob read and again right after decode, so a cancellation never
        waits for more than the already started frame. The internal lock is
        held only during the blob read — never during decode or while the
        caller accumulates NumPy data.

        Ownership: the generator closes this reader in its ``finally`` (job
        semantics). Do not call it on a shared reader such as the GUI frame
        cache; use a job-local instance instead.
        """
        try:
            for frame_index in frame_indices:
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Операция отменена")
                row = self.read_frame(int(frame_index))
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Операция отменена")
                yield row
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._source is not None:
                self._source.close()
                self._source = None


def _read_regular_sector_blob_from_handle(
    source: Any,
    chain: np.ndarray,
    sector_size: int,
    offset: int,
    length: int,
) -> bytes:
    position = int(offset)
    remaining = int(length)
    parts: list[bytes] = []
    while remaining > 0:
        logical_sector, within = divmod(position, sector_size)
        if logical_sector >= chain.size:
            break
        # CFB stream sectors are commonly allocated in consecutive physical
        # runs. Coalesce such a run into one buffered seek/read instead of
        # performing one kernel transition per 512-byte logical sector.
        max_sectors = min(
            chain.size - logical_sector,
            (remaining + within + sector_size - 1) // sector_size,
        )
        physical_sector = int(chain[logical_sector])
        run_sectors = 1
        while (
            run_sectors < max_sectors
            and int(chain[logical_sector + run_sectors]) == physical_sector + run_sectors
        ):
            run_sectors += 1
        count = min(remaining, run_sectors * sector_size - within)
        source.seek(sector_size + physical_sector * sector_size + within)
        data = source.read(count)
        if not data:
            break
        parts.append(data)
        consumed = len(data)
        position += consumed
        remaining -= consumed
    blob = b"".join(parts)
    if len(blob) != length:
        raise OSError(
            f"Неполное чтение spectrogram: ожидалось {length}, получено {len(blob)} байт"
        )
    return blob


def read_spectrogram_frame(
    path: str | Path,
    index: SpectrogramIndex,
    frame_index: int,
) -> SpectrogramRow:
    """Read one exact frame by indexed seek without scanning the full XML stream."""
    if not 0 <= frame_index < index.frame_count:
        raise IndexError(frame_index)
    offset = int(index.offsets[frame_index])
    length = int(index.lengths[frame_index])
    if index.sector_chain is not None and index.sector_size is not None:
        blob = _read_regular_sector_blob(
            path, index.sector_chain, index.sector_size, offset, length
        )
    else:
        with olefile.OleFileIO(Path(path)) as ole:
            stream, _chain = _open_lazy_stream(
                ole, index.info.source_stream, index.sector_chain
            )
            stream.seek(offset)
            blob = stream.read(length)
    row = _decode_line(blob, index.info.point_count)
    if row is None:
        raise ValueError(f"Не удалось декодировать кадр {frame_index}")
    return row
