"""Fixed-memory waterfall storage owned solely by the UI V2 presentation layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .sweep_rows import SweepRowStamp

_MEBIBYTE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class WaterfallPresentationBudget:
    """Source-local upper bound until UI2-07 verifies the runtime budget contract.

    The isolated UI V2 base intentionally has no importable runtime
    ``WaterfallRenderer``/``LiveDisplayResourceBudget``.  The values are a
    conservative presentation ceiling only; they are not device, DSP or
    acquisition configuration and are rechecked at integration.
    """

    max_rows: int = 4096
    max_columns: int = 2048
    max_total_bytes: int = 96 * _MEBIBYTE

    def estimate(self, rows: int, columns: int) -> int:
        if rows < 1 or columns < 1:
            raise ValueError("waterfall dimensions must be positive")
        if rows > self.max_rows or columns > self.max_columns:
            raise ValueError(
                f"waterfall dimensions exceed the UI V2 presentation budget: {rows}x{columns}"
            )
        # Two full float32 buffers are the conservative upper bound reserved
        # for ring plus renderer compatibility. The cyclic tile path below
        # normally retains only the ring itself.
        total = rows * columns * np.dtype(np.float32).itemsize * 2
        if total > self.max_total_bytes:
            raise ValueError("waterfall display budget exceeded")
        return total

    def max_history_seconds(self, rows_per_second: int) -> int:
        if rows_per_second < 1:
            raise ValueError("waterfall rows per second must be positive")
        self.estimate(1, self.max_columns)
        return max(1, self.max_rows // rows_per_second)

    def dimensions(self, history_seconds: int, rows_per_second: int, columns: int) -> tuple[int, int]:
        if history_seconds < 1 or rows_per_second < 1:
            raise ValueError("waterfall history and rows per second must be positive")
        rows = int(history_seconds) * int(rows_per_second)
        self.estimate(rows, int(columns))
        return rows, int(columns)


DEFAULT_WATERFALL_PRESENTATION_BUDGET = WaterfallPresentationBudget()


class BoundedWaterfallRing:
    """A single fixed float32 ring exposed as at most two zero-copy tiles."""

    def __init__(self, rows: int, columns: int) -> None:
        DEFAULT_WATERFALL_PRESENTATION_BUDGET.estimate(rows, columns)
        self._data: np.ndarray = np.zeros((int(rows), int(columns)), dtype=np.float32)
        # Keep one scalar timestamp per retained presentation row.  It is
        # deliberately separate from the image tiles, so irregular producer
        # time is neither resampled nor silently turned into render cadence.
        self._timestamps_ns: np.ndarray = np.zeros(int(rows), dtype=np.int64)
        self._write_index = 0
        self._count = 0
        self._sweep_stamps: list[SweepRowStamp | None] = [None] * int(rows)
        self._sweep_indices: dict[int, int] = {}
        self._latest_sweep_sequence: int | None = None

    @property
    def rows(self) -> int:
        return int(self._data.shape[0])

    @property
    def columns(self) -> int:
        return int(self._data.shape[1])

    @property
    def count(self) -> int:
        return self._count

    def clear(self) -> None:
        self._data.fill(0.0)
        self._write_index = 0
        self._count = 0
        self._sweep_stamps[:] = [None] * self.rows
        self._sweep_indices.clear()
        self._latest_sweep_sequence = None

    def append(self, values: np.ndarray, *, timestamp_ns: int, sweep_stamp: SweepRowStamp | None = None) -> None:
        row = np.asarray(values, dtype=np.float32)
        if row.ndim != 1 or row.size != self.columns:
            raise ValueError("waterfall row width changed")
        evicted = self._sweep_stamps[self._write_index]
        if evicted is not None:
            del self._sweep_indices[evicted.sequence]
        self._sweep_stamps[self._write_index] = sweep_stamp
        if sweep_stamp is not None:
            self._sweep_indices[sweep_stamp.sequence] = self._write_index
            self._latest_sweep_sequence = sweep_stamp.sequence
        self._data[self._write_index, :] = row
        self._timestamps_ns[self._write_index] = timestamp_ns
        self._write_index = (self._write_index + 1) % self.rows
        self._count = min(self._count + 1, self.rows)

    def upsert_sweep(self, values: np.ndarray, stamp: SweepRowStamp) -> Literal["append", "replace", "reject"]:
        """Update an existing pass in place, including a retained late terminal.

        The bounded index contains only retained rows. Missing/evicted old
        passes are never appended at the newest position. No synthetic rows,
        interpolation, power statistics or acquisition timestamps are created.
        """
        row = np.asarray(values, dtype=np.float32)
        if row.ndim != 1 or row.size != self.columns:
            raise ValueError("waterfall row width changed")
        index = self._sweep_indices.get(stamp.sequence)
        if index is not None:
            previous = self._sweep_stamps[index]
            if previous is None or not stamp.supersedes(previous):
                return "reject"
            self._data[index, :] = row
            self._sweep_stamps[index] = stamp
            return "replace"
        if self._latest_sweep_sequence is not None and stamp.sequence <= self._latest_sweep_sequence:
            return "reject"
        self.append(row, timestamp_ns=0, sweep_stamp=stamp)
        return "append"

    def chronological_sweep_stamps(self) -> tuple[SweepRowStamp | None, ...]:
        if self._count < self.rows or self._write_index == 0:
            return tuple(self._sweep_stamps[:self._count])
        return tuple(self._sweep_stamps[self._write_index:] + self._sweep_stamps[:self._write_index])

    def chronological_tiles(self) -> tuple[np.ndarray, ...]:
        """Return oldest→newest views with no concatenated staging image."""

        if self._count == 0:
            return ()
        if self._count < self.rows or self._write_index == 0:
            return (self._data[: self._count],)
        return (self._data[self._write_index :], self._data[: self._write_index])

    def chronological_timestamps_ns(self) -> np.ndarray:
        """Return an oldest-to-newest scalar view/copy-free slice pair collapsed only for axis use."""

        if self._count == 0:
            return self._timestamps_ns[:0]
        if self._count < self.rows or self._write_index == 0:
            return self._timestamps_ns[: self._count]
        # The axis needs a tiny, ordered scalar sequence.  Unlike image data,
        # this is at most 4096 int64 values and never a second full image.
        return np.concatenate((self._timestamps_ns[self._write_index :], self._timestamps_ns[: self._write_index]))


class BoundedWaterfallRenderer:
    """Small presentation adapter around one bounded ring; no decimation or DSP."""

    def __init__(self) -> None:
        self._buffer: BoundedWaterfallRing | None = None

    @property
    def buffer(self) -> BoundedWaterfallRing | None:
        return self._buffer

    def reset(self) -> None:
        self._buffer = None

    def clear(self) -> None:
        if self._buffer is not None:
            self._buffer.clear()

    def resize_rows(self, rows: int) -> None:
        """Resize a compatible local history ring, retaining its newest rows.

        This is presentation storage only.  A rate/history control change must
        not ask the producer to replay rows that have already been admitted.
        """

        buffer = self._buffer
        if buffer is None or buffer.rows == int(rows):
            return
        replacement = BoundedWaterfallRing(int(rows), buffer.columns)
        retained = min(buffer.count, replacement.rows)
        skip = buffer.count - retained
        timestamps = buffer.chronological_timestamps_ns()
        stamps = buffer.chronological_sweep_stamps()
        position = 0
        for tile in buffer.chronological_tiles():
            for row in tile:
                if position >= skip:
                    replacement.append(row, timestamp_ns=int(timestamps[position]), sweep_stamp=stamps[position])
                position += 1
        self._buffer = replacement

    def append(self, values: np.ndarray, *, rows: int, timestamp_ns: int) -> None:
        columns = int(np.asarray(values).size)
        if self._buffer is None or self._buffer.rows != rows or self._buffer.columns != columns:
            self._buffer = BoundedWaterfallRing(rows, columns)
        self._buffer.append(values, timestamp_ns=timestamp_ns)

    def tiles(self) -> tuple[np.ndarray, ...]:
        return () if self._buffer is None else self._buffer.chronological_tiles()

    def upsert_sweep(self, values: np.ndarray, *, rows: int, stamp: SweepRowStamp) -> Literal["append", "replace", "reject"]:
        columns = int(np.asarray(values).size)
        if self._buffer is None or self._buffer.rows != rows or self._buffer.columns != columns:
            self._buffer = BoundedWaterfallRing(rows, columns)
        return self._buffer.upsert_sweep(values, stamp)

    def sweep_stamps(self) -> tuple[SweepRowStamp | None, ...]:
        return () if self._buffer is None else self._buffer.chronological_sweep_stamps()

    def timestamps_ns(self) -> np.ndarray:
        return np.empty(0, dtype=np.int64) if self._buffer is None else self._buffer.chronological_timestamps_ns()
