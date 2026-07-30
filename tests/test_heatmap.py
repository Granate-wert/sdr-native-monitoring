from __future__ import annotations

import base64
import dataclasses
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.heatmap import (
    HeatmapAccumulator,
    HeatmapCache,
    HeatmapConfig,
    HeatmapGridMismatchError,
    HeatmapNormalization,
    HeatmapRangeMode,
    HeatmapRequest,
    HeatmapResult,
    HeatmapSamplingPolicy,
    density_hash,
    frequency_grid_hash,
    is_stale,
)
from esw_dfl.heatmap_worker import (
    compute_heatmap,
    resolve_frame_range,
    sample_positions,
    time_window_positions,
)
from esw_dfl.models import SpectrogramInfo
from esw_dfl.spectrogram import OperationCancelled, SpectrogramIndex, SpectrogramRow


FREQ_BINS = 8
POWER_MIN = -120.0
POWER_MAX = 0.0
POWER_BINS = 64
BIN_WIDTH = (POWER_MAX - POWER_MIN) / POWER_BINS  # 1.875, exact in binary fp


def _bin_center(bin_index: int) -> float:
    return POWER_MIN + (bin_index + 0.5) * BIN_WIDTH


def _config(**overrides: Any) -> HeatmapConfig:
    base: dict[str, Any] = {
        "range_mode": HeatmapRangeMode.FULL,
        "power_min_dbm": POWER_MIN,
        "power_max_dbm": POWER_MAX,
        "power_bins": POWER_BINS,
        "batch_size": 2,
    }
    base.update(overrides)
    return HeatmapConfig(**base)


def _request(config: HeatmapConfig | None = None, session: str = "s1", generation: int = 1) -> HeatmapRequest:
    return HeatmapRequest(
        session_id=session,
        waterfall_id="wf",
        source_id="src",
        config=config or _config(),
        generation=generation,
        frequency_grid_hash="grid",
    )


def _result(
    config: HeatmapConfig | None = None,
    generation: int = 1,
    processed: int = 3,
    density: np.ndarray | None = None,
) -> HeatmapResult:
    config = config or _config()
    if density is None:
        density = np.zeros((config.power_bins, FREQ_BINS), dtype=np.uint32)
    return HeatmapResult(
        density=density,
        frequencies_hz=np.linspace(100.0, 200.0, FREQ_BINS),
        power_axis_dbm=np.linspace(POWER_MIN, POWER_MAX, config.power_bins),
        processed_frames=processed,
        total_frames_in_range=processed,
        exact=True,
        sampling_policy=config.sampling_policy,
        config=config,
        generation=generation,
        frequency_grid_hash="grid",
    )


def _write_fake_dfl(
    root: Path,
    frames: list[np.ndarray],
    timestamps: np.ndarray | None = None,
) -> tuple[Path, SpectrogramInfo, SpectrogramIndex]:
    """Build a fake CFL-less file readable by SpectrogramFrameReader via a sector chain."""
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
    point_count = int(frames[0].size)
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=len(frames),
        point_count=point_count,
        start_hz=100.0,
        stop_hz=200.0,
    )
    if timestamps is None:
        timestamps = np.arange(len(frames), dtype=np.float64)
    index = SpectrogramIndex(
        info=info,
        line_indices=np.arange(len(frames), dtype=np.int64),
        timestamps=np.asarray(timestamps, dtype=np.float64),
        offsets=np.asarray(offsets, dtype=np.int64),
        lengths=np.asarray(lengths, dtype=np.int32),
        sector_chain=np.arange(sector_count, dtype=np.int32),
        sector_size=sector_size,
    )
    return path, info, index


def _compute(
    path: Path,
    info: SpectrogramInfo,
    index: SpectrogramIndex | None,
    config: HeatmapConfig,
    **kwargs: Any,
) -> HeatmapResult:
    frequencies = np.linspace(100.0, 200.0, info.point_count)
    return compute_heatmap(
        path,
        info,
        frequencies,
        config,
        generation=7,
        session_id="s1",
        waterfall_id="wf",
        source_id="src",
        index=index,
        **kwargs,
    )


class PowerToBinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)

    def test_power_to_bin_maps_bin_centers(self) -> None:
        values = np.array([_bin_center(k) for k in (0, 1, 10, 32, 63)])
        np.testing.assert_array_equal(self.acc.power_to_bin(values), np.array([0, 1, 10, 32, 63]))

    def test_lower_boundary_maps_to_bin_zero(self) -> None:
        self.assertEqual(int(self.acc.power_to_bin([POWER_MIN])[0]), 0)
        self.acc.add_frame(np.full(FREQ_BINS, POWER_MIN))
        self.assertEqual(int(self.acc.density[0].sum()), FREQ_BINS)

    def test_upper_boundary_maps_to_top_bin(self) -> None:
        self.assertEqual(int(self.acc.power_to_bin([POWER_MAX])[0]), POWER_BINS - 1)
        self.acc.add_frame(np.full(FREQ_BINS, POWER_MAX))
        self.assertEqual(int(self.acc.density[-1].sum()), FREQ_BINS)

    def test_out_of_range_values_clip_into_boundary_bins(self) -> None:
        # Documented policy: out-of-range values are clipped, not skipped.
        self.assertEqual(int(self.acc.power_to_bin([POWER_MIN - 50.0])[0]), 0)
        self.assertEqual(int(self.acc.power_to_bin([POWER_MAX + 50.0])[0]), POWER_BINS - 1)
        self.acc.add_frame(np.full(FREQ_BINS, POWER_MAX + 500.0))
        self.assertEqual(int(self.acc.density[-1].sum()), FREQ_BINS)
        self.assertEqual(int(self.acc.density.sum()), FREQ_BINS)

    def test_non_finite_values_are_skipped(self) -> None:
        values = np.array([np.nan, np.inf, -np.inf, _bin_center(5)] + [_bin_center(5)] * 4)
        bins = self.acc.power_to_bin(values)
        np.testing.assert_array_equal(bins[:3], np.array([-1, -1, -1]))
        self.acc.add_frame(values)
        self.assertEqual(int(self.acc.density[5].sum()), 5)
        self.assertEqual(int(self.acc.density.sum()), 5)


class AccumulatorTests(unittest.TestCase):
    def test_all_finite_batch_quantization_matches_scalar_reference(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        rows = np.vstack(
            [
                np.full(FREQ_BINS, _bin_center(3), dtype=np.float32),
                np.linspace(POWER_MIN - 5.0, POWER_MAX + 5.0, FREQ_BINS, dtype=np.float32),
                np.full(FREQ_BINS, _bin_center(47), dtype=np.float32),
            ]
        )
        destination = np.empty(rows.shape, dtype=np.uint16)
        acc.quantize_rows_into(rows, destination)
        expected = np.vstack([acc.power_to_bin(row) for row in rows]).astype(np.uint16)
        np.testing.assert_array_equal(destination, expected)

    def test_exact_accumulation_uses_uint32_density(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        self.assertEqual(acc.density.dtype, np.uint32)
        for _ in range(3):
            acc.add_frame(np.full(FREQ_BINS, _bin_center(10)))
        np.testing.assert_array_equal(acc.density[10], np.full(FREQ_BINS, 3, dtype=np.uint32))
        self.assertEqual(acc.frame_count, 3)
        self.assertFalse(acc.approximate)

    def test_clear_resets_density_but_keeps_buffers(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS, window_frames=4)
        acc.add_frame(np.full(FREQ_BINS, _bin_center(10)))
        before = acc.memory_bytes()
        acc.clear()
        self.assertEqual(int(acc.density.sum()), 0)
        self.assertEqual(acc.frame_count, 0)
        self.assertEqual(acc.memory_bytes(), before)

    def test_exponential_decay_is_approximate_weighted_persistence(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS, decay=0.5)
        self.assertEqual(acc.density.dtype, np.float64)
        self.assertTrue(acc.approximate)
        frame = np.full(FREQ_BINS, _bin_center(10))
        acc.add_frame(frame)
        acc.add_frame(frame)
        np.testing.assert_allclose(acc.density[10], np.full(FREQ_BINS, 1.5))
        acc.add_frame(frame)
        np.testing.assert_allclose(acc.density[10], np.full(FREQ_BINS, 1.75))

    def test_decay_add_frame_decays_weights_too(self) -> None:
        # Review finding: the decay branch must scale density AND W(k) by the
        # same factor, otherwise sum(D) diverges from sum(W).
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS, decay=0.5)
        frame = np.full(FREQ_BINS, _bin_center(5))
        acc.add_frame(frame)
        acc.add_frame(frame)
        self.assertAlmostEqual(float(acc.density[5].sum()), FREQ_BINS * 1.5)
        self.assertAlmostEqual(float(acc.normalization_weights_by_frequency[0]), 1.5)
        self.assertAlmostEqual(
            float(acc.density.sum()), float(acc.normalization_weights_by_frequency.sum())
        )

    def test_rolling_window_removes_oldest_frame_exactly(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS, window_frames=3)
        frames = [np.full(FREQ_BINS, _bin_center(k)) for k in (10, 20, 30, 40)]
        for frame in frames[:3]:
            acc.add_frame(frame)
        self.assertEqual(acc.frame_count, 3)
        acc.add_frame(frames[3])  # evicts the frame at bin 10
        self.assertEqual(int(acc.density[10].sum()), 0)
        for k in (20, 30, 40):
            np.testing.assert_array_equal(acc.density[k], np.ones(FREQ_BINS, dtype=np.uint32))
        self.assertEqual(acc.frame_count, 3)
        self.assertEqual(int(acc.density.sum()), 3 * FREQ_BINS)

    def test_ring_buffer_stores_compact_uint16_bins_not_full_frames(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS, window_frames=500)
        ring = acc.ring_buffer
        self.assertIsNotNone(ring)
        assert ring is not None
        self.assertEqual(ring.dtype, np.uint16)
        self.assertEqual(ring.shape, (500, FREQ_BINS))
        self.assertEqual(ring.nbytes, 500 * FREQ_BINS * 2)
        float_frames_bytes = 500 * FREQ_BINS * 4
        self.assertLess(ring.nbytes, float_frames_bytes)

    def test_memory_bytes_matches_documented_budget(self) -> None:
        acc = HeatmapAccumulator(1001, POWER_MIN, POWER_MAX, 256, window_frames=500)
        # density (uint32) + ring (uint16) + freq_index (int64) + V(k) weights (uint32)
        expected = 256 * 1001 * 4 + 500 * 1001 * 2 + 1001 * 8 + 1001 * 4
        self.assertEqual(acc.memory_bytes(), expected)
        self.assertLess(acc.memory_bytes(), 3 * 1024 * 1024)
        decay_acc = HeatmapAccumulator(1001, POWER_MIN, POWER_MAX, 256, decay=0.95)
        # float64 density + float64 W(k) weights + freq_index
        self.assertEqual(decay_acc.memory_bytes(), 256 * 1001 * 8 + 1001 * 8 + 1001 * 8)

    def test_grid_mismatch_raises(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        with self.assertRaises(HeatmapGridMismatchError):
            acc.add_frame(np.zeros(FREQ_BINS + 1))

    def test_non_finite_power_bounds_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HeatmapAccumulator(FREQ_BINS, float("nan"), POWER_MAX, POWER_BINS)
        with self.assertRaises(ValueError):
            HeatmapAccumulator(FREQ_BINS, POWER_MIN, float("inf"), POWER_BINS)

    def test_grid_mismatch_logs_diagnostic_event(self) -> None:
        acc = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        with self.assertLogs("esw_dfl.heatmap", level="WARNING") as captured:
            with self.assertRaises(HeatmapGridMismatchError):
                acc.add_frame(np.zeros(FREQ_BINS + 1))
        self.assertTrue(any("HEATMAP_GRID_MISMATCH" in message for message in captured.output))


class NormalizationTests(unittest.TestCase):
    def _density_fixture(self) -> np.ndarray:
        density = np.zeros((POWER_BINS, FREQ_BINS), dtype=np.uint32)
        density[10, 0] = 3
        return density

    def test_count_returns_raw_counts(self) -> None:
        result = _result(_config(normalization=HeatmapNormalization.COUNT), density=self._density_fixture())
        normalized = result.normalized()
        self.assertEqual(normalized.dtype, np.float64)
        self.assertEqual(float(normalized[10, 0]), 3.0)
        self.assertEqual(float(normalized.sum()), 3.0)

    def test_probability_divides_by_processed_frames(self) -> None:
        result = _result(
            _config(normalization=HeatmapNormalization.PROBABILITY), processed=3, density=self._density_fixture()
        )
        normalized = result.normalized()
        self.assertAlmostEqual(float(normalized[10, 0]), 1.0)

    def test_probability_with_zero_frames_returns_zeros(self) -> None:
        result = _result(
            _config(normalization=HeatmapNormalization.PROBABILITY), processed=0, density=self._density_fixture()
        )
        np.testing.assert_array_equal(result.normalized(), np.zeros_like(result.density, dtype=np.float64))

    def test_log_density_is_default_and_logarithmic(self) -> None:
        result = _result(density=self._density_fixture())
        self.assertIs(result.config.normalization, HeatmapNormalization.LOG_DENSITY)
        normalized = result.normalized()
        self.assertAlmostEqual(float(normalized[10, 0]), np.log10(4.0))
        self.assertEqual(float(normalized[0, 0]), 0.0)

    def test_normalized_accepts_mode_override_without_recompute(self) -> None:
        result = _result(density=self._density_fixture())
        np.testing.assert_array_equal(
            result.normalized(HeatmapNormalization.COUNT), result.density.astype(np.float64)
        )

    def test_normalized_never_shares_memory_with_internal_density(self) -> None:
        # float64 density: np.asarray would alias it, so this guards the copy.
        density = np.zeros((POWER_BINS, FREQ_BINS), dtype=np.float64)
        density[10, 0] = 3.0
        modes = (HeatmapNormalization.COUNT, HeatmapNormalization.PROBABILITY, HeatmapNormalization.LOG_DENSITY)
        for mode in modes:
            result = _result(_config(normalization=mode), processed=3, density=density)
            normalized = result.normalized()
            self.assertFalse(np.shares_memory(normalized, result.density))
            normalized[10, 0] = -999.0
            self.assertEqual(float(result.density[10, 0]), 3.0)


class ConfigValidationTests(unittest.TestCase):
    def test_power_min_must_be_below_power_max(self) -> None:
        with self.assertRaises(ValueError):
            _config(power_min_dbm=0.0, power_max_dbm=-120.0)

    def test_power_bins_whitelist(self) -> None:
        with self.assertRaises(ValueError):
            _config(power_bins=100)
        for allowed in (64, 128, 256, 512):
            _config(power_bins=allowed)

    def test_window_frames_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            _config(window_frames=0)

    def test_decay_must_be_within_unit_interval(self) -> None:
        with self.assertRaises(ValueError):
            _config(decay=1.5)
        with self.assertRaises(ValueError):
            _config(decay=-0.1)

    def test_selected_range_requires_bounds_and_non_empty(self) -> None:
        with self.assertRaises(ValueError):
            _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=5, frame_end=None)
        with self.assertRaises(ValueError):
            _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=9, frame_end=5)

    def test_time_window_requires_positive_window(self) -> None:
        with self.assertRaises(ValueError):
            _config(sampling_policy=HeatmapSamplingPolicy.TIME_WINDOW, time_window_s=None)
        with self.assertRaises(ValueError):
            _config(sampling_policy=HeatmapSamplingPolicy.TIME_WINDOW, time_window_s=0.0)


class RangeResolutionTests(unittest.TestCase):
    def test_last_n_window_ends_at_current_frame(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.LAST_N, window_frames=500)
        self.assertEqual(resolve_frame_range(config, 3000, 2500), (2001, 2500))

    def test_last_n_clips_at_file_start(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.LAST_N, window_frames=500)
        self.assertEqual(resolve_frame_range(config, 3000, 100), (0, 100))
        self.assertEqual(resolve_frame_range(config, 3000, None), (2500, 2999))

    def test_centered_window(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.CENTERED, window_frames=500)
        self.assertEqual(resolve_frame_range(config, 3000, 2500), (2250, 2749))

    def test_centered_window_shifts_inside_boundaries(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.CENTERED, window_frames=500)
        self.assertEqual(resolve_frame_range(config, 3000, 10), (0, 499))
        self.assertEqual(resolve_frame_range(config, 3000, 2999), (2500, 2999))

    def test_full_range(self) -> None:
        self.assertEqual(resolve_frame_range(_config(), 3000, 2500), (0, 2999))

    def test_exponential_decay_uses_last_n_semantics(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.EXPONENTIAL_DECAY, window_frames=500)
        self.assertEqual(resolve_frame_range(config, 3000, 2500), (2001, 2500))

    def test_selected_range_clipped_to_file(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=2900, frame_end=5000)
        self.assertEqual(resolve_frame_range(config, 3000, None), (2900, 2999))

    def test_single_frame_range(self) -> None:
        config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=7, frame_end=7)
        self.assertEqual(resolve_frame_range(config, 3000, None), (7, 7))

    def test_empty_ranges_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            resolve_frame_range(_config(), 0, None)
        config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=3000, frame_end=4000)
        with self.assertRaises(ValueError):
            resolve_frame_range(config, 3000, None)


class SamplingTests(unittest.TestCase):
    def test_sample_positions_are_deterministic_and_bounded(self) -> None:
        first = sample_positions(0, 9999, 100)
        second = sample_positions(0, 9999, 100)
        np.testing.assert_array_equal(first, second)
        self.assertLessEqual(first.size, 100)
        self.assertEqual(int(first[0]), 0)
        self.assertEqual(int(first[-1]), 9999)

    def test_sample_positions_return_full_range_when_it_fits(self) -> None:
        np.testing.assert_array_equal(sample_positions(0, 9, 4), np.array([0, 3, 6, 9]))
        np.testing.assert_array_equal(sample_positions(0, 9, 2000), np.arange(10))

    def test_time_window_positions_select_by_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _path, _info, index = _write_fake_dfl(Path(root), [np.zeros(FREQ_BINS, np.float32) for _ in range(10)])
            np.testing.assert_array_equal(time_window_positions(index, 9, 2.5), np.array([7, 8, 9]))

    def test_time_window_falls_back_to_newest_finite_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            timestamps = np.arange(10, dtype=np.float64)
            timestamps[9] = np.nan
            _path, _info, index = _write_fake_dfl(
                Path(root), [np.zeros(FREQ_BINS, np.float32) for _ in range(10)], timestamps=timestamps
            )
            np.testing.assert_array_equal(time_window_positions(index, 9, 1.0), np.array([7, 8]))

    def test_time_window_excludes_frames_after_current_with_equal_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            timestamps = np.array([0.0, 1.0, 2.0, 2.0, 2.0, 3.0])
            _path, _info, index = _write_fake_dfl(
                Path(root), [np.zeros(FREQ_BINS, np.float32) for _ in range(6)], timestamps=timestamps
            )
            np.testing.assert_array_equal(time_window_positions(index, 2, 10.0), np.array([0, 1, 2]))


class WorkerTests(unittest.TestCase):
    def test_exact_full_range_processes_every_frame(self) -> None:
        frames = [np.full(FREQ_BINS, _bin_center(32), dtype=np.float32) for _ in range(6)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            result = _compute(path, info, index, _config())
        self.assertTrue(result.exact)
        self.assertFalse(result.approximate)
        self.assertEqual(result.processed_frames, 6)
        self.assertEqual(result.total_frames_in_range, 6)
        self.assertEqual(int(result.density.sum()), 6 * FREQ_BINS)
        peak_bin = int(np.argmax(result.density.sum(axis=1)))
        self.assertEqual(peak_bin, 32)
        self.assertEqual(result.generation, 7)
        self.assertEqual(result.frequency_grid_hash, frequency_grid_hash(np.linspace(100.0, 200.0, FREQ_BINS)))
        self.assertEqual(result.density.shape, (POWER_BINS, FREQ_BINS))
        self.assertEqual(result.frequencies_hz.shape, (FREQ_BINS,))
        self.assertEqual(result.power_axis_dbm.shape, (POWER_BINS,))

    def test_last_n_at_file_start_processes_available_frames_only(self) -> None:
        frames = [np.full(FREQ_BINS, _bin_center(i), dtype=np.float32) for i in range(5)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            config = _config(range_mode=HeatmapRangeMode.LAST_N, window_frames=500)
            result = _compute(path, info, index, config, current_frame=2)
        self.assertEqual(result.processed_frames, 3)
        self.assertEqual(result.total_frames_in_range, 3)
        self.assertTrue(result.exact)

    def test_single_frame_selected_range(self) -> None:
        frames = [np.full(FREQ_BINS, _bin_center(i), dtype=np.float32) for i in range(5)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=3, frame_end=3)
            result = _compute(path, info, index, config)
        self.assertEqual(result.processed_frames, 1)
        self.assertTrue(result.exact)
        self.assertEqual(int(result.density[3].sum()), FREQ_BINS)
        self.assertEqual(int(result.density.sum()), FREQ_BINS)

    def test_sampled_range_is_deterministic_preview(self) -> None:
        frames = [np.full(FREQ_BINS, _bin_center(i % POWER_BINS), dtype=np.float32) for i in range(10)]
        config = _config(sampling_policy=HeatmapSamplingPolicy.SAMPLED_RANGE, max_preview_frames=4)
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            first = _compute(path, info, index, config)
            second = _compute(path, info, index, config)
        self.assertFalse(first.exact)
        self.assertEqual(first.processed_frames, 4)
        self.assertEqual(first.total_frames_in_range, 10)
        np.testing.assert_array_equal(first.density, second.density)

    def test_sampled_range_stays_exact_when_range_fits(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32) for _ in range(3)]
        config = _config(sampling_policy=HeatmapSamplingPolicy.SAMPLED_RANGE, max_preview_frames=2000)
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            result = _compute(path, info, index, config)
        self.assertTrue(result.exact)
        self.assertEqual(result.processed_frames, 3)

    def test_time_window_selects_frames_by_timestamp(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32) for _ in range(10)]
        config = _config(sampling_policy=HeatmapSamplingPolicy.TIME_WINDOW, time_window_s=2.5)
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            result = _compute(path, info, index, config, current_frame=9)
        self.assertEqual(result.processed_frames, 3)
        self.assertEqual(result.total_frames_in_range, 3)
        self.assertTrue(result.exact)

    def test_time_window_requires_index(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        config = _config(sampling_policy=HeatmapSamplingPolicy.TIME_WINDOW, time_window_s=2.5)
        with tempfile.TemporaryDirectory() as root:
            path, info, _index = _write_fake_dfl(Path(root), frames)
            with self.assertRaises(ValueError):
                _compute(path, info, None, config)

    def test_legacy_exponential_decay_worker_is_rejected(self) -> None:
        frames = [np.full(FREQ_BINS, _bin_center(10), dtype=np.float32) for _ in range(4)]
        config = _config(range_mode=HeatmapRangeMode.EXPONENTIAL_DECAY, window_frames=10, decay=0.5)
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            with self.assertRaisesRegex(ValueError, "PersistenceEngine"):
                _compute(path, info, index, config, current_frame=3)

    def test_frequency_grid_mismatch_aborts(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            wrong_grid = np.linspace(100.0, 200.0, FREQ_BINS + 1)
            with self.assertRaises(HeatmapGridMismatchError):
                compute_heatmap(path, info, wrong_grid, _config(), 1, "s", "w", "src", index=index)

    def test_frame_width_mismatch_aborts(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32), np.zeros(4, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            with self.assertRaises(HeatmapGridMismatchError):
                _compute(path, info, index, _config())

    def test_cancel_before_start(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32) for _ in range(4)]
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            with self.assertRaises(OperationCancelled):
                _compute(path, info, index, _config(), cancel=cancel)

    def test_cancel_between_batches(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32) for _ in range(20)]
        cancel = threading.Event()
        calls: list[tuple[int, int]] = []

        def progress(processed: int, total: int) -> None:
            calls.append((processed, total))
            cancel.set()

        clock = {"t": 0.0}

        def fake_monotonic() -> float:
            clock["t"] += 1.0
            return clock["t"]

        config = _config(batch_size=5)
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            with patch("esw_dfl.heatmap_worker.time.perf_counter", side_effect=fake_monotonic):
                with self.assertRaises(OperationCancelled):
                    _compute(path, info, index, config, progress=progress, cancel=cancel)
        self.assertEqual(calls, [(5, 20)])

    def test_progress_is_throttled_and_final_call_reports_totals(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32) for _ in range(20)]
        config = _config(batch_size=1)
        clock = {"t": 0.0}

        def run(step: float) -> list[tuple[int, int]]:
            calls: list[tuple[int, int]] = []

            def fake_monotonic() -> float:
                clock["t"] += step
                return clock["t"]

            with tempfile.TemporaryDirectory() as root:
                path, info, index = _write_fake_dfl(Path(root), frames)
                with patch("esw_dfl.heatmap_worker.time.perf_counter", side_effect=fake_monotonic):
                    _compute(path, info, index, config, progress=lambda p, t: calls.append((p, t)))
            return calls

        fast_clock_calls = run(0.2)  # every batch emits
        slow_clock_calls = run(0.01)  # only every ~10th batch emits
        self.assertEqual(fast_clock_calls[-1], (20, 20))
        self.assertEqual(slow_clock_calls[-1], (20, 20))
        self.assertLess(len(slow_clock_calls), len(fast_clock_calls))

    def test_streaming_fallback_maps_positions_to_line_indices(self) -> None:
        rows = [
            SpectrogramRow(i, float(i), np.full(FREQ_BINS, _bin_center(10 + i), dtype=np.float32))
            for i in (1, 2, 3)
        ]
        captured: dict[str, Any] = {}

        def fake_iter(path: Any, info: Any, selected: Any = None, *args: Any, **kwargs: Any) -> Any:
            captured["selected"] = selected
            return iter(list(rows))

        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=1, frame_end=3)
        with tempfile.TemporaryDirectory() as root:
            path, info, _index = _write_fake_dfl(Path(root), frames)
            info = dataclasses.replace(info, line_count=10)
            with patch("esw_dfl.heatmap_worker.iter_spectrogram_rows", side_effect=fake_iter):
                result = _compute(path, info, None, config)
        self.assertEqual(captured["selected"], {1, 2, 3})
        self.assertEqual(result.processed_frames, 3)
        self.assertEqual(int(result.density.sum()), 3 * FREQ_BINS)

    def test_streaming_fallback_marks_inexact_when_source_lines_missing(self) -> None:
        rows = [
            SpectrogramRow(i, float(i), np.zeros(FREQ_BINS, dtype=np.float32))
            for i in (1, 2)  # line 3 of the requested {1, 2, 3} is absent from the file
        ]

        def fake_iter(path: Any, info: Any, selected: Any = None, *args: Any, **kwargs: Any) -> Any:
            return iter(list(rows))

        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=1, frame_end=3)
        with tempfile.TemporaryDirectory() as root:
            path, info, _index = _write_fake_dfl(Path(root), frames)
            info = dataclasses.replace(info, line_count=10)
            with patch("esw_dfl.heatmap_worker.iter_spectrogram_rows", side_effect=fake_iter):
                result = _compute(path, info, None, config)
        self.assertEqual(result.processed_frames, 2)
        self.assertEqual(result.total_frames_in_range, 3)
        self.assertFalse(result.exact)

    def test_frequency_grid_mismatch_logs_diagnostic_event(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            wrong_grid = np.linspace(100.0, 200.0, FREQ_BINS + 1)
            with self.assertLogs("esw_dfl.heatmap", level="WARNING") as captured:
                with self.assertRaises(HeatmapGridMismatchError):
                    compute_heatmap(path, info, wrong_grid, _config(), 1, "s", "w", "src", index=index)
        self.assertTrue(any("HEATMAP_GRID_MISMATCH" in message for message in captured.output))

    def test_cancel_after_final_incomplete_batch_raises(self) -> None:
        cancel = threading.Event()

        def fake_iter(path: Any, info: Any, selected: Any = None, *args: Any, **kwargs: Any) -> Any:
            def generate() -> Any:
                for i in range(3):
                    yield SpectrogramRow(i, float(i), np.zeros(FREQ_BINS, dtype=np.float32))
                cancel.set()  # cancel lands after the last, incomplete batch

            return generate()

        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        config = _config(range_mode=HeatmapRangeMode.SELECTED, frame_start=0, frame_end=2, batch_size=100)
        with tempfile.TemporaryDirectory() as root:
            path, info, _index = _write_fake_dfl(Path(root), frames)
            info = dataclasses.replace(info, line_count=3)
            with patch("esw_dfl.heatmap_worker.iter_spectrogram_rows", side_effect=fake_iter):
                with self.assertRaises(OperationCancelled):
                    _compute(path, info, None, config, cancel=cancel)

    def test_result_frequency_grid_does_not_alias_caller_array(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            grid = np.linspace(100.0, 200.0, FREQ_BINS)
            result = compute_heatmap(path, info, grid, _config(), 1, "s", "w", "src", index=index)
            self.assertFalse(np.shares_memory(result.frequencies_hz, grid))
            grid[:] = -1.0
            np.testing.assert_array_equal(result.frequencies_hz, np.linspace(100.0, 200.0, FREQ_BINS))

    def test_worker_result_carries_utc_computed_at(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            before = datetime.now(timezone.utc)
            result = compute_heatmap(path, info, np.linspace(100.0, 200.0, FREQ_BINS), _config(), 1, "s", "w", "src", index=index)
            after = datetime.now(timezone.utc)
        self.assertIsNotNone(result.computed_at)
        parsed = datetime.fromisoformat(str(result.computed_at))
        self.assertIsNotNone(parsed.tzinfo)
        self.assertLessEqual(before, parsed)
        self.assertLessEqual(parsed, after)


class GenerationGuardTests(unittest.TestCase):
    def test_stale_result_is_detected_by_generation(self) -> None:
        result = _result(generation=3)
        self.assertFalse(is_stale(result, 3))
        self.assertTrue(is_stale(result, 4))

    def test_worker_result_carries_request_generation(self) -> None:
        frames = [np.zeros(FREQ_BINS, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as root:
            path, info, index = _write_fake_dfl(Path(root), frames)
            result = _compute(path, info, index, _config())
        self.assertEqual(result.generation, 7)


class HashTests(unittest.TestCase):
    def test_frequency_grid_hash_is_stable_and_sensitive(self) -> None:
        grid = np.linspace(100.0, 200.0, FREQ_BINS)
        self.assertEqual(frequency_grid_hash(grid), frequency_grid_hash(grid.copy()))
        self.assertNotEqual(frequency_grid_hash(grid), frequency_grid_hash(grid + 1.0))
        self.assertNotEqual(frequency_grid_hash(grid), frequency_grid_hash(grid[:-1]))

    def test_density_hash_is_normalization_independent(self) -> None:
        density = np.arange(POWER_BINS * FREQ_BINS, dtype=np.uint32).reshape(POWER_BINS, FREQ_BINS)
        count_result = _result(_config(normalization=HeatmapNormalization.COUNT), density=density)
        log_result = _result(_config(normalization=HeatmapNormalization.LOG_DENSITY), density=density)
        self.assertEqual(density_hash(count_result.density), density_hash(log_result.density))


class CacheTests(unittest.TestCase):
    def test_cache_key_ignores_normalization(self) -> None:
        key_a = HeatmapCache.make_key(_request(_config(normalization=HeatmapNormalization.COUNT)))
        key_b = HeatmapCache.make_key(_request(_config(normalization=HeatmapNormalization.LOG_DENSITY)))
        self.assertEqual(key_a, key_b)

    def test_cache_key_has_no_visual_styling_inputs(self) -> None:
        # Palette/opacity/interpolation are not part of config or key by design.
        field_names = {field.name for field in dataclasses.fields(HeatmapConfig)}
        self.assertTrue(field_names.isdisjoint({"palette", "opacity", "interpolation"}))

    def test_cache_key_changes_with_power_range(self) -> None:
        key_a = HeatmapCache.make_key(_request())
        key_b = HeatmapCache.make_key(_request(_config(power_min_dbm=-100.0)))
        self.assertNotEqual(key_a, key_b)

    def test_cache_key_distinguishes_range_parameters(self) -> None:
        last_n_500 = HeatmapCache.make_key(_request(_config(range_mode=HeatmapRangeMode.LAST_N, window_frames=500)))
        last_n_1000 = HeatmapCache.make_key(_request(_config(range_mode=HeatmapRangeMode.LAST_N, window_frames=1000)))
        self.assertNotEqual(last_n_500, last_n_1000)
        decay_half = HeatmapCache.make_key(_request(_config(range_mode=HeatmapRangeMode.EXPONENTIAL_DECAY, decay=0.5)))
        decay_ninth = HeatmapCache.make_key(_request(_config(range_mode=HeatmapRangeMode.EXPONENTIAL_DECAY, decay=0.9)))
        self.assertNotEqual(decay_half, decay_ninth)
        self.assertNotEqual(last_n_500, HeatmapCache.make_key(_request()))
        time_a = HeatmapCache.make_key(
            _request(_config(sampling_policy=HeatmapSamplingPolicy.TIME_WINDOW, time_window_s=1.0))
        )
        time_b = HeatmapCache.make_key(
            _request(_config(sampling_policy=HeatmapSamplingPolicy.TIME_WINDOW, time_window_s=2.0))
        )
        self.assertNotEqual(time_a, time_b)
        preview_a = HeatmapCache.make_key(
            _request(_config(sampling_policy=HeatmapSamplingPolicy.SAMPLED_RANGE, max_preview_frames=100))
        )
        preview_b = HeatmapCache.make_key(
            _request(_config(sampling_policy=HeatmapSamplingPolicy.SAMPLED_RANGE, max_preview_frames=200))
        )
        self.assertNotEqual(preview_a, preview_b)

    def test_get_put_and_hit_ratio(self) -> None:
        cache = HeatmapCache()
        key = HeatmapCache.make_key(_request())
        self.assertIsNone(cache.get(key))
        self.assertEqual(cache.misses, 1)
        result = _result()
        self.assertTrue(cache.put(key, result))
        self.assertIs(cache.get(key), result)
        self.assertEqual(cache.hits, 1)
        self.assertAlmostEqual(cache.cache_hit_ratio, 0.5)

    def test_lru_eviction_respects_memory_budget(self) -> None:
        entry_size = POWER_BINS * FREQ_BINS * 4 + FREQ_BINS * 8 + POWER_BINS * 8
        cache = HeatmapCache(budget_bytes=2 * entry_size + 1)
        keys = [HeatmapCache.make_key(_request(_config(frame_start=i, frame_end=i,
                                                       range_mode=HeatmapRangeMode.SELECTED)))
                for i in range(3)]
        for key in keys:
            self.assertTrue(cache.put(key, _result()))
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get(keys[0]))  # least recently used evicted
        self.assertIsNotNone(cache.get(keys[1]))
        self.assertIsNotNone(cache.get(keys[2]))
        self.assertLessEqual(cache.total_size_bytes, cache.budget_bytes)

    def test_oversized_entry_is_not_stored(self) -> None:
        cache = HeatmapCache(budget_bytes=10)
        self.assertFalse(cache.put(HeatmapCache.make_key(_request()), _result()))
        self.assertEqual(len(cache), 0)

    def test_oversized_put_with_same_key_keeps_existing_entry(self) -> None:
        entry_size = POWER_BINS * FREQ_BINS * 4 + FREQ_BINS * 8 + POWER_BINS * 8
        cache = HeatmapCache(budget_bytes=entry_size)
        key = HeatmapCache.make_key(_request())
        small = _result()
        self.assertTrue(cache.put(key, small))
        big = _result(_config(power_bins=512), density=np.zeros((512, 1001), dtype=np.uint32))
        self.assertFalse(cache.put(key, big))
        self.assertIs(cache.get(key), small)

    def test_invalidate_session_removes_only_that_session(self) -> None:
        cache = HeatmapCache()
        key_a = HeatmapCache.make_key(_request(session="a"))
        key_b = HeatmapCache.make_key(_request(session="b"))
        cache.put(key_a, _result())
        cache.put(key_b, _result())
        self.assertEqual(cache.invalidate_session("a"), 1)
        self.assertIsNone(cache.get(key_a))
        self.assertIsNotNone(cache.get(key_b))
        self.assertEqual(cache.invalidate_session("a"), 0)

    def test_clear_empties_entries_and_keeps_counters(self) -> None:
        cache = HeatmapCache()
        key = HeatmapCache.make_key(_request())
        cache.put(key, _result())
        cache.get(key)
        cache.clear()
        self.assertEqual(len(cache), 0)
        self.assertEqual(cache.total_size_bytes, 0)
        self.assertEqual(cache.hits, 1)


if __name__ == "__main__":
    unittest.main()
