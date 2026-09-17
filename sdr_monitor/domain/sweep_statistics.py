"""Immutable native Sweep statistics. No DSP or accumulation in Python/Qt.

Density cells pool finite native-bin observations, NOT per-pass peak hits.
Average retains every measurement bin. A publication may lag current Sweep.
"""
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True, slots=True)
class SweepStatisticsSettings:
    window_passes: int = 32
    power_bins: int = 64
    power_min_db: float = -160.0
    power_max_db: float = 10.0
    density_columns: int = 1024
    max_payload_bytes: int = 512 * 1024 * 1024
    snapshot_rate_hz: float = 15.0

    def __post_init__(self) -> None:
        for value, upper in ((self.window_passes, 1024), (self.power_bins, 256),
                             (self.density_columns, 2048), (self.max_payload_bytes, 512 * 1024 * 1024)):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError("Sweep statistics setting exceeds bounded integer range")
        if (not all(type(x) in (int, float) and math.isfinite(x)
                    for x in (self.power_min_db, self.power_max_db, self.snapshot_rate_hz))
                or not -300 <= self.power_min_db < self.power_max_db <= 100
                or not 1 <= self.snapshot_rate_hz <= 60):
            raise ValueError("invalid Sweep statistics level/rate range")

    def payload_upper_bound(self, bins: int, retained_slots: int) -> int:
        """Conservative native payload, not RSS; includes 4 downstream UI owners.

        Pass metadata uses 32 bytes (native sizeof is <=32), shared axis/edges
        counted once. No frequency-grid or FFT reduction to make a plan fit.
        """
        if type(bins) is not int or not 2 <= bins <= 2_000_000 or retained_slots < 1:
            raise ValueError("invalid statistics preflight geometry")
        columns = min(bins, self.density_columns)
        cells = columns * self.power_bins
        state = bins * (4 * self.window_passes + 28) + 32 * self.window_passes
        state += 4 * cells + 4 * columns + 8 * (columns + 1)
        snapshot = 8 * bins + 8 * cells + 4 * columns
        result = state + (retained_slots + 4) * snapshot
        if result > self.max_payload_bytes:
            raise ValueError("Sweep statistics and retained snapshots exceed memory budget")
        return result


@dataclass(frozen=True, slots=True)
class SweepStatisticsFrame:
    source_id: str
    epoch: int
    update_sequence: int
    newest_pass_sequence: int
    unique_passes_seen: int
    retained_passes: int
    unit: str
    power_min_db: float
    power_max_db: float
    frequencies_hz: np.ndarray
    average_db: np.ndarray
    observations: np.ndarray
    density_frequency_edges_hz: np.ndarray
    density_observations: np.ndarray
    histogram_counts: np.ndarray
    probability: np.ndarray

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.unit.strip():
            raise ValueError("Sweep statistics requires source and unit")
        for value in (self.epoch, self.update_sequence, self.newest_pass_sequence,
                      self.unique_passes_seen, self.retained_passes):
            if type(value) is not int or value < 0:
                raise ValueError("Sweep statistics identity/counters must be nonnegative integers")
        if not 1 <= self.retained_passes <= self.unique_passes_seen <= self.update_sequence:
            raise ValueError("Sweep statistics pass/revision counters are inconsistent")
        arrays = (
            (self.frequencies_hz, np.float64, 1), (self.average_db, np.float32, 1),
            (self.observations, np.uint32, 1), (self.density_frequency_edges_hz, np.float64, 1),
            (self.density_observations, np.uint32, 1), (self.histogram_counts, np.uint32, 2),
            (self.probability, np.float32, 2),
        )
        if any(not isinstance(a, np.ndarray) or a.flags.writeable or a.ndim != dim
               or a.dtype != dtype for a, dtype, dim in arrays):
            raise ValueError("Sweep statistics arrays must be typed immutable native views")
        n = self.frequencies_hz.size
        d = self.density_observations.size
        p = self.probability.shape[0]
        if (not 2 <= n <= 2_000_000 or not 1 <= d <= n or not 1 <= p <= 256
                or self.average_db.size != n or self.observations.size != n
                or self.density_frequency_edges_hz.size != d + 1
                or self.probability.shape != (p, d) or self.histogram_counts.shape != (p, d)):
            raise ValueError("Sweep statistics array geometry mismatch")
        if (not math.isfinite(self.power_min_db) or not math.isfinite(self.power_max_db)
                or not self.power_min_db < self.power_max_db):
            raise ValueError("invalid density power edges")
        for axis in (self.frequencies_hz, self.density_frequency_edges_hz):
            delta = np.diff(axis)
            tolerance = max(abs(float(np.spacing(axis[0]))) * 8, abs(float(delta[0])) * 1e-7)
            if (not np.all(np.isfinite(axis)) or np.any(delta <= 0)
                    or not np.allclose(delta, delta[0], rtol=0, atol=tolerance)):
                raise ValueError("Sweep statistics requires regular physical frequency geometry")
        spacing = (self.frequencies_hz[-1] - self.frequencies_hz[0]) / (n - 1)
        if not np.allclose(self.density_frequency_edges_hz[[0, -1]],
                           self.frequencies_hz[[0, -1]] + [-spacing / 2, spacing / 2],
                           rtol=0, atol=max(spacing * 1e-7, abs(np.spacing(self.frequencies_hz[-1])) * 8)):
            raise ValueError("density cells must cover the measurement bin edges")
        if (np.any(self.observations > self.retained_passes) or np.any(np.isposinf(self.average_db))
                or not np.array_equal(np.isnan(self.average_db), self.observations == 0)):
            raise ValueError("average observations must preserve unknown bins")
        if not np.array_equal(self.histogram_counts.sum(axis=0, dtype=np.uint64), self.density_observations):
            raise ValueError("density histogram denominators are inconsistent")
        missing = self.density_observations == 0
        if (np.any(~np.isnan(self.probability[:, missing]))
                or np.any(~np.isfinite(self.probability[:, ~missing]))
                or np.any(self.probability[:, ~missing] < 0) or np.any(self.probability[:, ~missing] > 1)):
            raise ValueError("density must preserve unknown cells and probabilities")
        # Validate a producer result; never use this calculation as a render source.
        if not np.allclose(self.probability[:, ~missing] * self.density_observations[~missing],
                           self.histogram_counts[:, ~missing], rtol=1e-6, atol=1e-6):
            raise ValueError("native density probability differs from its observation counts")

    @property
    def values(self) -> np.ndarray:
        return self.average_db

    def validate_parent(self, source_id: str, epoch: int, sequence: int,
                        unit: str, frequencies_hz: np.ndarray) -> None:
        if (self.source_id != source_id or self.epoch != epoch or self.unit != unit
                or self.newest_pass_sequence > sequence
                or not np.array_equal(self.frequencies_hz, frequencies_hz)):
            raise ValueError("Sweep statistics source/epoch/grid/unit/sequence mismatches parent")
