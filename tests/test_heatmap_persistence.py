"""Pure-engine tests for the Heatmap persistence contracts (review P0/§10).

Covers: exact offline equality, subtract semantics and underflow guard,
rolling ring vs offline reference, expired-frame eviction, NaN per-column
Probability oracle, time-window inclusive boundary, decay half-life formula,
irregular timestamp deltas, Pause invariance and state validation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl import heatmap_worker
from esw_dfl.frame_navigation import NavigationReason
from esw_dfl.heatmap import (
    HeatmapAccumulator,
    HeatmapConfig,
    HeatmapNormalization,
    HeatmapRangeMode,
    HeatmapResult,
    HeatmapSamplingPolicy,
    density_hash,
)
from esw_dfl.heatmap_persistence import (
    PersistenceConfig,
    PersistenceEngine,
    PersistenceMode,
    PersistenceSourceKey,
    PersistenceTarget,
    PersistenceWorkRequest,
    RollingExactState,
    WindowUnit,
    decay_history_positions,
    heatmap_render_budget,
)
from esw_dfl.heatmap_worker import compute_heatmap
from heatmap_persistence_fixtures import (
    FREQ_BINS,
    POWER_BINS,
    POWER_MAX,
    POWER_MIN,
    CountingFrameReader,
    SyntheticPersistenceSource,
    make_fixture_ab,
    make_frame,
    make_index,
    offline_density,
)


SOURCE_KEY = PersistenceSourceKey("session", "waterfall", "stream", "grid")
FREQUENCIES = np.linspace(100.0, 800.0, FREQ_BINS)



class HeatmapRenderBudgetTests(unittest.TestCase):
    def test_rt_1001_budget_uses_ui_refresh_and_safety_margin(self) -> None:
        budget = heatmap_render_budget(30, instrument_sweep_time_s=82.016e-6)
        self.assertTrue(budget.available)
        self.assertEqual(budget.required_frames_per_refresh, 407)
        self.assertEqual(budget.recommended_window_frames, 611)

    def test_timestamp_speed_scales_frame_and_time_floors(self) -> None:
        budget = heatmap_render_budget(
            60, instrument_sweep_time_s=82.016e-6, playback_speed=2.0
        )
        self.assertEqual(budget.playback_speed, 2.0)
        self.assertEqual(budget.required_frames_per_refresh, 407)
        self.assertEqual(budget.recommended_window_frames, 611)
        self.assertAlmostEqual(
            budget.recommended_window_seconds or 0.0,
            611 * 82.016e-6,
        )

    def test_fastest_of_declared_and_recorded_periods_is_binding(self) -> None:
        budget = heatmap_render_budget(
            20, instrument_sweep_time_s=0.100, recorded_period_s=0.020
        )
        self.assertEqual(budget.effective_frame_period_s, 0.020)
        self.assertEqual(budget.required_frames_per_refresh, 3)
        self.assertEqual(budget.recommended_window_frames, 5)

    def test_unknown_timing_keeps_no_artificial_floor(self) -> None:
        budget = heatmap_render_budget(60)
        self.assertFalse(budget.available)
        self.assertEqual(budget.recommended_window_frames, 1)

    def test_rolling_config_rejects_smaller_than_render_budget(self) -> None:
        with self.assertRaises(ValueError):
            PersistenceConfig(
                mode=PersistenceMode.ROLLING_EXACT,
                window_frames=610,
                minimum_window_frames=611,
            )

def _request(
    source: SyntheticPersistenceSource,
    *,
    target_frame: int,
    window_frames: int = 5,
    mode: PersistenceMode = PersistenceMode.ROLLING_EXACT,
    unit: WindowUnit = WindowUnit.FRAMES,
    window_seconds: float | None = None,
    half_life: float | None = None,
    generation: int = 1,
    target_timestamp: float | None = None,
) -> PersistenceWorkRequest:
    config = PersistenceConfig(
        mode=mode,
        window_unit=unit,
        window_frames=window_frames,
        window_seconds=window_seconds,
        half_life_seconds=half_life,
        power_min_dbm=POWER_MIN,
        power_max_dbm=POWER_MAX,
        power_bins=POWER_BINS,
    )
    if target_timestamp is None:
        target_timestamp = float(source.timestamps[target_frame])
    return PersistenceWorkRequest(
        source_key=SOURCE_KEY,
        config=config,
        generation=generation,
        navigation_generation=generation,
        target_frame=target_frame,
        target_timestamp=target_timestamp,
        frame_count=source.frame_count,
        frequencies_hz=FREQUENCIES,
    )


def _target(source: SyntheticPersistenceSource, frame_index: int, generation: int) -> PersistenceTarget:
    return PersistenceTarget(
        source_key=SOURCE_KEY,
        frame_index=frame_index,
        timestamp=float(source.timestamps[frame_index]),
        navigation_generation=generation,
        persistence_generation=generation,
        reason=NavigationReason.API,
    )


class ExactEngineTests(unittest.TestCase):
    def test_exact_offline_equality(self) -> None:
        frames = make_fixture_ab()
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, snapshot = engine.rebuild_exact(
            _request(source, target_frame=199, window_frames=50), source
        )
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 150, 199))
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (150, 199))
        self.assertTrue(snapshot.exact)
        self.assertFalse(snapshot.approximate)
        self.assertEqual(snapshot.processed_frames, 50)
        np.testing.assert_array_equal(
            snapshot.normalization_weights_by_frequency,
            np.full(FREQ_BINS, 50, dtype=np.uint32),
        )
        self.assertEqual(len(state.contributions), 50)
        self.assertEqual(snapshot.target_frame, 199)

    def test_snapshot_does_not_share_engine_buffers(self) -> None:
        frames = make_fixture_ab()[:20]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, snapshot = engine.rebuild_exact(_request(source, target_frame=4), source)
        snapshot_density_before = snapshot.density.copy()
        snapshot.density[...] = 0
        snapshot.normalization_weights_by_frequency[...] = 0
        np.testing.assert_array_equal(state.accumulator.density, snapshot_density_before)
        self.assertGreater(int(state.accumulator.normalization_weights_by_frequency.sum()), 0)

    def test_subtract_contribution_and_underflow_guard(self) -> None:
        accumulator = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        contribution = accumulator.make_contribution(0, None, make_frame(3))
        accumulator.add_contribution(contribution)
        self.assertEqual(int(accumulator.density.sum()), FREQ_BINS)
        accumulator.subtract_contribution(contribution)
        self.assertEqual(int(accumulator.density.sum()), 0)
        self.assertEqual(int(accumulator.normalization_weights_by_frequency.sum()), 0)
        with self.assertRaises(RuntimeError):
            accumulator.subtract_contribution(contribution)

    def test_exact_ring_matches_offline_reference(self) -> None:
        frames = [make_frame(index % FREQ_BINS) for index in range(20)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_exact(_request(source, target_frame=4), source)
        snapshot = None
        for target_frame in range(5, 20):
            snapshot = engine.advance_exact(state, _target(source, target_frame, target_frame + 1), source)
            self.assertIsNotNone(snapshot)
        assert snapshot is not None
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 15, 19))
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (15, 19))
        self.assertEqual(len(state.contributions), 5)
        # Sequential advance reads only the entering frame.
        self.assertEqual(source.read_indices, list(range(0, 20)))

    def test_expired_frame_is_subtracted(self) -> None:
        frames = [make_frame(0, power_dbm=-40.0)] + [make_frame(3) for _ in range(5)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, snapshot = engine.rebuild_exact(_request(source, target_frame=4), source)
        probe = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        unique_bin = int(probe.power_to_bin(np.array([-40.0]))[0])
        self.assertEqual(int(snapshot.density[unique_bin, 0]), 1)
        snapshot = engine.advance_exact(state, _target(source, 5, 2), source)
        assert snapshot is not None
        self.assertEqual(int(snapshot.density[unique_bin, 0]), 0)
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 1, 5))
        self.assertEqual(int(snapshot.density.sum()), 5 * FREQ_BINS)

    def test_non_overlapping_jump_requires_rebuild(self) -> None:
        frames = make_fixture_ab()
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_exact(
            _request(source, target_frame=99, window_frames=50), source
        )
        reads_before = len(source.read_indices)
        result = engine.advance_exact(state, _target(source, 250, 2), source)
        self.assertIsNone(result)
        self.assertEqual(len(source.read_indices), reads_before)  # no reads on the rebuild path

    def test_stream_range_consumes_intermediate_frames_without_snapshot_per_frame(self) -> None:
        frames = make_fixture_ab()
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_exact(
            _request(source, target_frame=99, window_frames=50), source
        )
        reads_before = len(source.read_indices)
        snapshot = engine.advance_exact_range(state, _target(source, 250, 2), source)
        assert snapshot is not None
        self.assertEqual(source.read_indices[reads_before:], list(range(100, 251)))
        self.assertEqual(snapshot.target_frame, 250)
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (201, 250))
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 201, 250))

    def test_frame_window_uses_zero_copy_cyclic_uint16_contributions(self) -> None:
        frames = [make_frame(index % FREQ_BINS) for index in range(24)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_exact(
            _request(source, target_frame=4, window_frames=5), source
        )
        ring = state.contribution_ring
        assert ring is not None
        self.assertEqual(ring.dtype, np.uint16)
        self.assertEqual(ring.shape, (5, FREQ_BINS))
        self.assertTrue(all(np.shares_memory(item.bin_indices, ring) for item in state.contributions))

        snapshot = engine.advance_exact_range(state, _target(source, 19, 2), source)
        assert snapshot is not None
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (15, 19))
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 15, 19))
        self.assertTrue(all(np.shares_memory(item.bin_indices, ring) for item in state.contributions))
        self.assertEqual(state.contribution_ring_cursor, 0)
    def test_advance_on_empty_state_reads_only_target_window(self) -> None:
        frames = [make_frame(index % FREQ_BINS) for index in range(20)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state = RollingExactState.empty(_request(source, target_frame=9))
        snapshot = engine.advance_exact(state, _target(source, 9, 2), source)
        assert snapshot is not None
        self.assertEqual(source.read_indices, [5, 6, 7, 8, 9])
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (5, 9))
        self.assertEqual(len(state.contributions), 5)
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 5, 9))

    def test_advance_on_empty_time_window_state_requires_rebuild(self) -> None:
        frames = [make_frame(index % FREQ_BINS) for index in range(20)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state = RollingExactState.empty(
            _request(source, target_frame=9, unit=WindowUnit.SECONDS, window_seconds=2.0)
        )
        # Reading the whole prefix to resolve a time window is never the
        # engine's choice; the caller rebuilds with explicit positions.
        snapshot = engine.advance_exact(state, _target(source, 9, 2), source)
        self.assertIsNone(snapshot)
        self.assertEqual(source.read_indices, [])

    def test_rebuild_exact_empty_positions_raises(self) -> None:
        frames = [make_frame(1) for _ in range(4)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        with self.assertRaises(ValueError):
            engine.rebuild_exact(_request(source, target_frame=3), source, positions=[])
        with self.assertRaises(ValueError):
            engine.rebuild_decay(
                _request(source, target_frame=3, mode=PersistenceMode.EXPONENTIAL_DECAY, half_life=1.0),
                source,
                positions=[],
            )

    def test_time_window_inclusive_boundary(self) -> None:
        timestamps = np.arange(6, dtype=np.float64)
        frames = [make_frame(index % FREQ_BINS) for index in range(6)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        # t=4.0, window 2.0 s -> [2.0, 4.0]: frames 2,3,4 (cutoff inclusive).
        request = _request(
            source,
            target_frame=4,
            unit=WindowUnit.SECONDS,
            window_seconds=2.0,
        )
        state, snapshot = engine.rebuild_exact(request, source, positions=range(0, 5))
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 2, 4))
        # Advance to t=5.0 -> [3.0, 5.0]: frame 2 (t=2.0) evicted, t=3.0 stays.
        snapshot = engine.advance_exact(state, _target(source, 5, 2), source)
        assert snapshot is not None
        np.testing.assert_array_equal(snapshot.density, offline_density(frames, 3, 5))
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (3, 5))


    def test_time_window_skips_missing_timestamp_on_advance(self) -> None:
        timestamps = np.array([0.0, 1.0, np.nan, 3.0])
        frames = [make_frame(index % FREQ_BINS) for index in range(4)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        request = _request(
            source,
            target_frame=1,
            unit=WindowUnit.SECONDS,
            window_seconds=1.0,
        )
        state, _snapshot = engine.rebuild_exact(request, source, positions=[0, 1])
        snapshot = engine.advance_exact(state, _target(source, 3, 2), source)
        assert snapshot is not None
        # t=3, window 1 s -> only the frame at t=3 belongs to the window;
        # the NaN-timestamp frame is not silently retained.
        np.testing.assert_array_equal(snapshot.density, offline_density([frames[3]], 0, 0))
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (3, 3))
        self.assertEqual(snapshot.processed_frames, 1)


class DecayEngineTests(unittest.TestCase):
    def test_decay_half_life_halves_previous_contribution(self) -> None:
        timestamps = np.array([0.0, 1.0])
        frames = [make_frame(2), make_frame(None)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_decay(
            _request(
                source,
                target_frame=0,
                mode=PersistenceMode.EXPONENTIAL_DECAY,
                half_life=1.0,
            ),
            source,
            positions=[0],
        )
        snapshot = engine.advance_decay(state, _target(source, 1, 2), source)
        assert snapshot is not None
        probe = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        signal_bin = int(probe.power_to_bin(np.array([-50.0]))[0])
        noise_bin = int(probe.power_to_bin(np.array([-100.0]))[0])
        # After dt == half_life the old contribution is exactly halved.
        self.assertAlmostEqual(float(snapshot.density[signal_bin, 2]), 0.5)
        # Column 2 of frame 0 held the signal, so its noise row only decays
        # frame 1's fresh noise; column 0 carries both noise contributions.
        self.assertAlmostEqual(float(snapshot.density[noise_bin, 2]), 1.0)
        self.assertAlmostEqual(float(snapshot.density[noise_bin, 0]), 0.5 + 1.0)
        self.assertAlmostEqual(float(snapshot.normalization_weights_by_frequency[2]), 1.5)
        self.assertFalse(snapshot.exact)
        self.assertTrue(snapshot.approximate)
        self.assertEqual(snapshot.half_life_seconds, 1.0)

    def test_irregular_timestamp_delta_clamps_to_zero(self) -> None:
        timestamps = np.array([0.0, 0.0, -5.0])  # duplicate then non-monotonic
        frames = [make_frame(1), make_frame(1), make_frame(1)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        state, snapshot = engine.rebuild_decay(
            _request(
                source,
                target_frame=2,
                mode=PersistenceMode.EXPONENTIAL_DECAY,
                half_life=1.0,
            ),
            source,
        )
        # dt clamped at zero => alpha == 1: decayed density equals the plain sum.
        probe = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        for frame in frames:
            probe.add_frame(frame)
        np.testing.assert_allclose(snapshot.density, probe.density, rtol=0, atol=0)

    def test_decay_requires_timestamps(self) -> None:
        timestamps = np.array([np.nan, np.nan])
        frames = [make_frame(1), make_frame(1)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        with self.assertRaises(ValueError):
            engine.rebuild_decay(
                _request(
                    source,
                    target_frame=1,
                    mode=PersistenceMode.EXPONENTIAL_DECAY,
                    half_life=1.0,
                    target_timestamp=None,
                ),
                source,
            )

    def test_decay_irregular_delta_factors(self) -> None:
        # dt = 0.1 half-lives -> alpha = 2**-0.1; dt = 1.0 half-life -> 0.5.
        half_life = 2.0
        timestamps = np.array([0.0, 0.2, 2.2])
        frames = [make_frame(1), make_frame(1), make_frame(1)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        _state, snapshot = engine.rebuild_decay(
            _request(
                source,
                target_frame=2,
                mode=PersistenceMode.EXPONENTIAL_DECAY,
                half_life=half_life,
            ),
            source,
        )
        probe = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        signal_bin = int(probe.power_to_bin(np.array([-50.0]))[0])
        alpha_01 = 2.0 ** -0.1
        alpha_10 = 0.5
        # Frame 0 weighs alpha_01 * alpha_10, frame 1 weighs alpha_10, frame 2 weighs 1.
        expected = alpha_01 * alpha_10 + alpha_10 + 1.0
        self.assertAlmostEqual(float(snapshot.density[signal_bin, 1]), expected, places=9)
        expected_weight = expected  # W(k) accumulates the same factors
        self.assertAlmostEqual(
            float(snapshot.normalization_weights_by_frequency[1]), expected_weight, places=9
        )

    def test_bounded_history_weight_drops_below_epsilon(self) -> None:
        half_life = 1.0
        epsilon = 1e-3
        # T_history = log2(1/1e-3) ≈ 9.97 s: frame at t=0 is ~12 half-lives old.
        timestamps = np.array([0.0, 11.0, 12.0])
        frames = [make_frame(2), make_frame(3), make_frame(None)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        config = PersistenceConfig(
            mode=PersistenceMode.EXPONENTIAL_DECAY,
            half_life_seconds=half_life,
            decay_cutoff_epsilon=epsilon,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
        )
        request = PersistenceWorkRequest(
            source_key=SOURCE_KEY,
            config=config,
            generation=1,
            navigation_generation=1,
            target_frame=2,
            target_timestamp=12.0,
            frame_count=3,
            frequencies_hz=FREQUENCIES,
            timestamps=timestamps,
        )
        _state, snapshot = engine.rebuild_decay(request, source)
        probe = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        signal_bin = int(probe.power_to_bin(np.array([-50.0]))[0])
        # Bounded rebuild excludes frames older than T_history entirely.
        self.assertEqual(float(snapshot.density[signal_bin, 2]), 0.0)
        # A frame at exactly one half-life keeps half of its weight.
        self.assertAlmostEqual(float(snapshot.density[signal_bin, 3]), 0.5)
        self.assertEqual(snapshot.history_start_frame, 1)  # t=0 outside the bounded history
        self.assertEqual(snapshot.history_end_frame, 2)
        self.assertEqual(snapshot.half_life_seconds, half_life)
        self.assertEqual(snapshot.decay_cutoff_epsilon, epsilon)
        self.assertTrue(snapshot.approximate)
        self.assertFalse(snapshot.exact)

    def test_persistence_config_rejects_invalid_decay_epsilon(self) -> None:
        for epsilon in (None, -1e-3, 0.0, 1.0, 1.1, float("nan")):
            with self.subTest(epsilon=epsilon):
                with self.assertRaises(ValueError):
                    PersistenceConfig(
                        mode=PersistenceMode.EXPONENTIAL_DECAY,
                        half_life_seconds=1.0,
                        decay_cutoff_epsilon=epsilon,
                    )

    def test_seconds_window_requires_positive_duration(self) -> None:
        for window_seconds in (None, -1.0, 0.0, float("nan")):
            with self.subTest(window_seconds=window_seconds):
                with self.assertRaises(ValueError):
                    PersistenceConfig(
                        mode=PersistenceMode.ROLLING_EXACT,
                        window_unit=WindowUnit.SECONDS,
                        window_seconds=window_seconds,
                    )

    def test_rolling_seconds_config_rejects_lower_than_render_budget(self) -> None:
        with self.assertRaises(ValueError):
            PersistenceConfig(
                mode=PersistenceMode.ROLLING_EXACT,
                window_unit=WindowUnit.SECONDS,
                window_seconds=0.049,
                minimum_window_seconds=0.050,
            )

    def test_decay_history_positions_respects_bounds(self) -> None:
        config = PersistenceConfig(
            mode=PersistenceMode.EXPONENTIAL_DECAY,
            half_life_seconds=1.0,
            decay_cutoff_epsilon=1e-3,
        )
        timestamps = np.array([0.0, 5.0, 9.97, 9.98, 10.0, np.nan])
        positions = decay_history_positions(config, 10.0, timestamps)
        # Inclusive cutoff: t == target - T_history stays; NaN never enters.
        np.testing.assert_array_equal(positions, np.array([1, 2, 3, 4], dtype=np.int64))

    def test_pause_does_not_change_density(self) -> None:
        timestamps = np.arange(4, dtype=np.float64)
        frames = [make_frame(index % FREQ_BINS) for index in range(4)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        state, snapshot = engine.rebuild_decay(
            _request(
                source,
                target_frame=3,
                mode=PersistenceMode.EXPONENTIAL_DECAY,
                half_life=0.5,
            ),
            source,
        )
        hash_before = density_hash(state.accumulator.density)
        weights_before = state.accumulator.normalization_weights_by_frequency.copy()
        reads_before = len(source.read_indices)
        repeated = engine.advance_decay(state, _target(source, 3, 5), source)
        assert repeated is not None
        self.assertEqual(density_hash(state.accumulator.density), hash_before)
        np.testing.assert_array_equal(
            state.accumulator.normalization_weights_by_frequency, weights_before
        )
        self.assertEqual(len(source.read_indices), reads_before)  # Pause performs no reads
        # The exact path is equally idempotent on a repeated target.
        exact_state, _ = engine.rebuild_exact(_request(source, target_frame=3), source)
        exact_hash = density_hash(exact_state.accumulator.density)
        reads_before = len(source.read_indices)
        repeated_exact = engine.advance_exact(exact_state, _target(source, 3, 6), source)
        assert repeated_exact is not None
        self.assertEqual(density_hash(exact_state.accumulator.density), exact_hash)
        self.assertEqual(len(source.read_indices), reads_before)


class ProbabilityWeightsTests(unittest.TestCase):
    def test_nan_per_column_probability_oracle(self) -> None:
        frames = []
        for _index in range(4):
            frame = make_frame(3, power_dbm=-60.0)
            frame[7] = np.nan
            frames.append(frame)
        accumulator = HeatmapAccumulator(FREQ_BINS, POWER_MIN, POWER_MAX, POWER_BINS)
        for frame in frames:
            accumulator.add_frame(frame)
        config = HeatmapConfig(
            range_mode=HeatmapRangeMode.FULL,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
            normalization=HeatmapNormalization.PROBABILITY,
        )
        result = HeatmapResult(
            density=accumulator.density.copy(),
            frequencies_hz=FREQUENCIES,
            power_axis_dbm=accumulator.power_axis_dbm(),
            processed_frames=4,
            total_frames_in_range=4,
            exact=True,
            sampling_policy=HeatmapSamplingPolicy.FULL_RANGE,
            config=config,
            generation=1,
            frequency_grid_hash="grid",
            normalization_weights_by_frequency=accumulator.normalization_weights_by_frequency.copy(),
        )
        probability = result.normalized()
        # Valid columns: the distribution sums to 1; the all-NaN column to 0.
        for column in range(FREQ_BINS):
            expected = 0.0 if column == 7 else 1.0
            self.assertAlmostEqual(float(probability[:, column].sum()), expected)
        self.assertEqual(int(result.normalization_weights_by_frequency[7]), 0)
        self.assertEqual(int(result.normalization_weights_by_frequency[3]), 4)

    def test_compute_heatmap_result_carries_per_column_weights(self) -> None:
        frames = []
        for _index in range(4):
            frame = make_frame(3, power_dbm=-60.0)
            frame[7] = np.nan
            frames.append(frame)
        reader = CountingFrameReader(frames)
        index = make_index(len(frames))
        config = HeatmapConfig(
            range_mode=HeatmapRangeMode.FULL,
            power_min_dbm=POWER_MIN,
            power_max_dbm=POWER_MAX,
            power_bins=POWER_BINS,
        )
        with patch.object(heatmap_worker, "SpectrogramFrameReader", lambda _path, _index: reader):
            result = compute_heatmap(
                "synthetic.dfl",
                index.info,
                FREQUENCIES,
                config,
                generation=1,
                session_id="s",
                waterfall_id="w",
                source_id="src",
                index=index,
            )
        weights = result.normalization_weights_by_frequency
        assert weights is not None
        self.assertEqual(int(weights[7]), 0)
        self.assertEqual(int(weights[3]), 4)
        probability = result.normalized(HeatmapNormalization.PROBABILITY)
        self.assertAlmostEqual(float(probability[:, 7].sum()), 0.0)
        self.assertAlmostEqual(float(probability[:, 3].sum()), 1.0)


class ValidationTests(unittest.TestCase):
    def _valid_state(self):
        frames = [make_frame(index % FREQ_BINS) for index in range(10)]
        source = SyntheticPersistenceSource(frames)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_exact(_request(source, target_frame=5), source)
        return engine, state

    def test_valid_state_passes(self) -> None:
        engine, state = self._valid_state()
        engine.validate(state)  # must not raise

    def test_unsorted_contributions_fail_validation(self) -> None:
        engine, state = self._valid_state()
        state.contributions.append(state.contributions[0])
        with self.assertRaises(ValueError):
            engine.validate(state)

    def test_weight_mismatch_fails_validation(self) -> None:
        engine, state = self._valid_state()
        state.accumulator.normalization_weights_by_frequency[0] += 1
        with self.assertRaises(ValueError):
            engine.validate(state)

    def test_deque_gap_fails_validation(self) -> None:
        engine, state = self._valid_state()
        del state.contributions[2]  # hole in the middle of a FRAMES window
        with self.assertRaises(ValueError):
            engine.validate(state)

    def test_decay_state_validates_sums(self) -> None:
        timestamps = np.arange(4, dtype=np.float64)
        frames = [make_frame(1) for _ in range(4)]
        source = SyntheticPersistenceSource(frames, timestamps)
        engine = PersistenceEngine()
        state, _snapshot = engine.rebuild_decay(
            _request(
                source,
                target_frame=3,
                mode=PersistenceMode.EXPONENTIAL_DECAY,
                half_life=1.0,
            ),
            source,
        )
        engine.validate(state)
        state.accumulator.density[0, 0] += 1.0
        with self.assertRaises(ValueError):
            engine.validate(state)


if __name__ == "__main__":
    unittest.main()

