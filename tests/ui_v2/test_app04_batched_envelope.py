"""Exact equivalence to the pre-batching reducer, including gaps and ties."""
from math import ceil, isnan
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum.contracts import SpectrumFrameView
from sdr_monitor.ui.v2.spectrum.envelope import peak_preserving_envelope
from sdr_monitor.ui.v2.spectrum.envelope_batch import BATCH_SAMPLES, bucket_batches, extrema_rows
from sdr_monitor.ui.v2.spectrum.sweep_coverage import SweepCoverageState
from tests.ui_v2.test_app04_sweep_coverage import line, snapshot


def reference_envelope(frequencies, values, width):
    """Frozen scalar algorithm from de5843b; independent of production helper."""
    width = max(1, int(width))
    if len(values) <= width * 4:
        return frequencies, values
    x, y = [], []

    def gap():
        if not y or not isnan(y[-1]):
            x.append(float("nan"))
            y.append(float("nan"))

    size = ceil(len(values) / width)
    for start in range(0, len(values), size):
        stop = min(len(values), start + size)
        finite = np.flatnonzero(np.isfinite(values[start:stop]))
        if not len(finite):
            gap()
            continue
        if finite[0] != 0:
            gap()
        indices = start + finite
        local = values[indices]
        selected = {indices[0], indices[-1], indices[np.argmin(local)], indices[np.argmax(local)]}
        previous = indices[0]
        for index in sorted(selected):
            if index > previous and not np.all(np.isfinite(values[previous:index])):
                gap()
            x.append(float(frequencies[index]))
            y.append(float(values[index]))
            previous = index
        if finite[-1] != stop - start - 1:
            gap()
    return np.asarray(x), np.asarray(y)


def reference_coverage(current, previous, width):
    """Original full-view per-column coverage work, for numerical and timing A/B."""
    width = max(1, min(2048, width))
    size = ceil(current.values_db.size / width)
    states, x, y = [], [], []
    for start in range(0, current.values_db.size, size):
        stop = min(current.values_db.size, start + size)
        valid = np.isfinite(current.values_db[start:stop])
        old = previous.values_db[start:stop] if previous is not None else np.full(stop - start, np.nan)
        history = ~valid & np.isfinite(old)
        absent = ~valid & ~history
        states.append((1 if valid.any() else 0) | (2 if history.any() else 0) | (4 if absent.any() else 0))
        if previous is not None:
            fx, fy = reference_envelope(current.frequencies_hz[start:stop], np.where(history, old, np.nan), 1)
            x.extend(fx)
            y.extend(fy)
    return np.asarray(states), np.asarray(x), np.asarray(y)


class BatchedEnvelopeTests(unittest.TestCase):
    def test_scalar_equivalence_for_random_gaps_ties_extrema_and_batch_boundaries(self):
        rng = np.random.default_rng(19471)
        for count in (2, 3, 4, 5, 17, 65, 257, 4099, 65537, 131075):
            for kind in ("dense", "gaps", "alternating", "absent", "ties"):
                values = rng.integers(-110, -10, count * 2).astype(np.float32)[::2]
                frequencies = np.cumsum(rng.random(count * 2) + .01)[::2]
                if kind == "gaps":
                    values[rng.random(count) < .4] = np.nan
                elif kind == "alternating":
                    values[::2] = np.nan
                elif kind == "absent":
                    values[:] = np.nan
                elif kind == "ties":
                    values[:] = -70
                values.setflags(write=False)
                frequencies.setflags(write=False)
                for width in (0, 1, 2, 7, 128, 1920):
                    with self.subTest(count=count, kind=kind, width=width):
                        expected_x, expected_y = reference_envelope(frequencies, values, width)
                        actual = peak_preserving_envelope(SpectrumFrameView(None, frequencies, values, "dBFS/bin"), width)
                        np.testing.assert_equal(actual.frequencies_hz, expected_x)
                        np.testing.assert_equal(actual.values, expected_y)
                        self.assertLessEqual(actual.display_point_count, max(1, width) * 9)

    def test_coverage_matches_original_per_bucket_history_and_all_state_bits(self):
        rng = np.random.default_rng(667)
        for count in (9, 103, 8193, 131071):
            old_values = rng.normal(-80, 10, count)
            new_values = rng.normal(-70, 5, count)
            old_values[rng.random(count) < .25] = np.nan
            new_values[rng.random(count) < .5] = np.nan
            old, new = line(1, old_values), line(2, new_values)
            state = SweepCoverageState()
            state.accept(snapshot(old))
            state.accept(snapshot(new))
            for width in (1, 2, 7, 1920, 10000):
                with self.subTest(count=count, width=width):
                    flags, x, y = reference_coverage(new, old, width)
                    actual = state.project(0, 3e9, width)
                    np.testing.assert_equal(actual.states, flags)
                    np.testing.assert_equal(actual.history.frequencies_hz, x)
                    np.testing.assert_equal(actual.history.values, y)

    def test_batch_scratch_bound_and_single_wide_bucket(self):
        for count, size in ((2_000_000, 1042), (2_000_000, 2_000_000), (19, 3)):
            batches = list(bucket_batches(count, size))
            self.assertEqual(sum(rows * width for _, rows, width in batches), count)
            self.assertTrue(all(rows * width <= max(BATCH_SAMPLES, size) for _, rows, width in batches))
            self.assertEqual([start for start, _, _ in batches],
                             list(np.cumsum([0] + [rows * width for _, rows, width in batches[:-1]])))
        values = np.arange(2_000_000, dtype=np.float32)
        values[100:200] = np.nan
        frame = SpectrumFrameView(None, np.arange(values.size, dtype=np.float64), values, "dBFS/bin")
        with patch("sdr_monitor.ui.v2.spectrum.envelope.extrema_rows", wraps=extrema_rows) as reducer:
            result = peak_preserving_envelope(frame, 1920)
            self.assertLess(reducer.call_count, 40)  # Batches, not 1,920 calls.
        self.assertLessEqual(result.display_point_count, 9 * 1920)
        np.testing.assert_equal(result.values, reference_envelope(frame.frequencies_hz, values, 1920)[1])
