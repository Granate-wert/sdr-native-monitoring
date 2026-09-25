"""Bounded observer-only history-stroke experiment, not a product quality gate."""

import io
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import numpy as np

from scripts.benchmark_app05_all_layers import (
    _MAX_BOUNDED_PROBE_INPUT,
    _bounded_noisy_fragments,
    main,
)


class AllLayerProbeTests(unittest.TestCase):
    def test_dotted_and_dashdot_fragments_keep_each_exact_source_endpoint(self):
        edges = np.array(((2.0, 3.0, 12.0, 23.0),
                          (19.0, -4.0, 29.0, 6.0)), dtype=np.float64)
        frozen = edges.copy()
        for dotted, fragments_per_edge in ((True, 5), (False, 4)):
            fragments = _bounded_noisy_fragments(edges, dotted=dotted)
            self.assertEqual(fragments.shape, (len(edges) * fragments_per_edge, 4))
            per_edge = fragments.reshape(len(edges), fragments_per_edge, 4)
            np.testing.assert_array_equal(per_edge[:, 0, :2], edges[:, :2])
            np.testing.assert_array_equal(per_edge[:, -1, 2:], edges[:, 2:])
            for original, pieces in zip(edges, per_edge, strict=True):
                delta = original[2:] - original[:2]
                for first_x, first_y, last_x, last_y in pieces:
                    for x, y in ((first_x, first_y), (last_x, last_y)):
                        fraction = (x - original[0]) / delta[0]
                        self.assertGreaterEqual(fraction, 0.0)
                        self.assertLessEqual(fraction, 1.0)
                        self.assertAlmostEqual(y, original[1] + fraction * delta[1])
        np.testing.assert_array_equal(edges, frozen)

    def test_nonfinite_and_overcap_input_are_rejected_before_fragment_allocations(self):
        with self.assertRaisesRegex(ValueError, "finite source edges"):
            _bounded_noisy_fragments(np.array([[0.0, 0.0, np.nan, 1.0]]), dotted=True)
        with self.assertRaisesRegex(ValueError, "input contract"):
            _bounded_noisy_fragments(np.zeros((_MAX_BOUNDED_PROBE_INPUT + 1, 4)), dotted=True)

    def test_cli_rejects_silent_solid_mode_and_unsupported_full_input(self):
        for extra in (("--bounded-noisy-edge-probe",),
                      ("--bounded-noisy-edge-probe", "--attribute-history-geometry",
                       "--bins", "65536")):
            with self.subTest(extra=extra), patch.object(
                sys, "argv", ["benchmark_app05_all_layers.py", "--output", "unused.json",
                              "--capture-dir", "unused", *extra]
            ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
