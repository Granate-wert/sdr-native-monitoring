"""Native reduced-statistics contract, budget and capsule lifetime; no RF I/O."""
from dataclasses import replace
import gc
import importlib
from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.sweep_statistics import SweepStatisticsFrame, SweepStatisticsSettings
from sdr_monitor.services.native_continuous_sweep import (
    _to_domain_line, _to_domain_progress, _to_domain_statistics, _SweepStatisticsCache,
)
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory


def immutable(values, dtype):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def statistics_fixture():
    return SweepStatisticsFrame(
        "fake-sweep", 7, 1, 1, 1, 1, "dBFS/bin", -100., 0.,
        immutable([100e6, 101e6, 102e6, 103e6], np.float64),
        immutable([-80, -70, np.nan, np.nan], np.float32),
        immutable([1, 1, 0, 0], np.uint32),
        immutable([99.5e6, 101.5e6, 103.5e6], np.float64),
        immutable([2, 0], np.uint32),
        immutable([[2, 0], [0, 0]], np.uint32),
        immutable([[1, np.nan], [0, np.nan]], np.float32),
    )


class SweepStatisticsContractTests(unittest.TestCase):
    def test_parent_keeps_lag_but_rejects_future_foreign_epoch_grid_and_unit(self):
        frame = statistics_fixture()
        frame.validate_parent(frame.source_id, 7, 3, frame.unit, frame.frequencies_hz)
        for source, epoch, sequence, unit, grid in (
            ("other", 7, 1, frame.unit, frame.frequencies_hz),
            (frame.source_id, 8, 1, frame.unit, frame.frequencies_hz),
            (frame.source_id, 7, 0, frame.unit, frame.frequencies_hz),
            (frame.source_id, 7, 1, "dBm", frame.frequencies_hz),
            (frame.source_id, 7, 1, frame.unit, frame.frequencies_hz + 1),
        ):
            with self.assertRaises(ValueError):
                frame.validate_parent(source, epoch, sequence, unit, grid)

    def test_mutable_malformed_nonphysical_and_invented_density_rejected(self):
        frame = statistics_fixture()
        for changes in (
            {"average_db": frame.average_db.copy()}, {"epoch": True},
            {"retained_passes": 2}, {"power_max_db": float("inf")},
            {"probability": immutable([[0.5, np.nan], [0.5, np.nan]], np.float32)},
            {"probability": immutable([[1, 0], [0, 0]], np.float32)},
            {"average_db": immutable([-80, -70, -90, -90], np.float32)},
            {"density_frequency_edges_hz": immutable([99e6, 101e6, 103e6], np.float64)},
            {"density_observations": immutable([1, 0], np.uint32)},
            {"frequencies_hz": immutable([100e6, 101e6, 103e6, 104e6], np.float64)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(frame, **changes)

    def test_absent_native_statistics_stays_absent_and_view_is_not_copied(self):
        self.assertIsNone(_to_domain_statistics(SimpleNamespace()))
        frame = statistics_fixture()
        native = SimpleNamespace(**{name: getattr(frame, name) for name in frame.__dataclass_fields__})
        converted = _to_domain_statistics(SimpleNamespace(statistics=native))
        self.assertIs(converted.probability, frame.probability)
        self.assertIs(converted.average_db, frame.average_db)

    def test_budget_preflight_is_explicit_bounded_and_does_not_change_fft(self):
        live = LiveConfiguration(sample_rate_hz=61.44e6, fft_size=4096, backend=BackendKind.CPU)
        plain = ContinuousSweepPlanRequest(100e6, 500e6)
        base = NativeContinuousSweepPlanFactory.preflight_profile(live, plain)
        configured = replace(plain, statistics=SweepStatisticsSettings())
        admitted = NativeContinuousSweepPlanFactory.preflight_profile(live, configured)
        self.assertEqual(base.statistics_payload_bytes, 0)
        self.assertGreater(admitted.statistics_payload_bytes, 0)
        self.assertEqual(base.physical_fft_size, admitted.physical_fft_size)
        self.assertEqual(base.reduced, admitted.reduced)
        with self.assertRaisesRegex(ValueError, "memory budget"):
            NativeContinuousSweepPlanFactory.preflight_profile(live, replace(
                configured, statistics=replace(configured.statistics, max_payload_bytes=1024)))
        for setting in ({"window_passes": 0}, {"density_columns": 8192}, {"snapshot_rate_hz": 61}):
            with self.assertRaises(ValueError):
                SweepStatisticsSettings(**setting)


class CompiledSweepStatisticsTests(unittest.TestCase):
    def test_native_frames_survive_conversion_wrapper_destruction_and_gc(self):
        native = importlib.import_module("sdr_monitor._sdr_native")
        partial, final = native._make_test_sweep_statistics_frames(4096)
        cache = _SweepStatisticsCache()
        first = _to_domain_statistics(partial, cache=cache)
        self.assertIs(first, _to_domain_statistics(partial, cache=cache))
        with self.assertRaises(AttributeError):
            partial.statistics.epoch = 99
        progress, terminal = _to_domain_progress(partial), _to_domain_line(final)
        statistics = bundle_from_sweep(progress).sweep_statistics
        self.assertEqual(statistics.probability.shape, (64, 128))
        self.assertEqual(statistics.average_db.size, 4096)
        self.assertEqual(statistics.unique_passes_seen, 17)
        self.assertEqual(statistics.retained_passes, 16)
        self.assertEqual(statistics.observations[0], 16)
        self.assertEqual(statistics.observations[-1], 15)
        self.assertEqual(terminal.statistics.observations[-1], 16)
        self.assertEqual(terminal.statistics.unique_passes_seen, 17)
        expected = statistics.probability.copy()
        del partial, final, progress, terminal
        gc.collect()
        np.testing.assert_array_equal(statistics.probability, expected)
        for array in (statistics.average_db, statistics.observations, statistics.histogram_counts,
                      statistics.probability, statistics.density_frequency_edges_hz):
            with self.assertRaises(ValueError):
                array.setflags(write=True)

    def test_python_conservative_bound_exceeds_exact_native_payload(self):
        native = importlib.import_module("sdr_monitor._sdr_native")
        settings = SweepStatisticsSettings()
        config = native.SweepStatisticsConfig(settings.window_passes, settings.power_bins,
            settings.power_min_db, settings.power_max_db, settings.max_payload_bytes, settings.density_columns)
        for bins, slots in ((4, 1), (600, 1050), (900_000, 12)):
            self.assertGreater(settings.payload_upper_bound(bins, slots), config.required_payload_bytes(bins, slots))


if __name__ == "__main__":
    unittest.main()
