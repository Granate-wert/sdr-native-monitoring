"""Compiled native test-frame buffers outlive their Python frame wrapper; no RX."""

import gc
import importlib
import unittest

import numpy as np


class NativeBufferLifetimeTests(unittest.TestCase):
    def test_retained_arrays_survive_native_frame_replacement_and_gc(self):
        native = importlib.import_module("sdr_monitor._sdr_native")
        frame = native._make_test_spectrum_frame(4096, frame_sequence=1)
        frequencies, values = frame.frequencies_hz, frame.values
        expected_frequencies, expected_values = frequencies.copy(), values.copy()
        self.assertIsNotNone(frequencies.base)
        self.assertIsNotNone(values.base)
        for sequence in range(2, 102):
            frame = native._make_test_spectrum_frame(4096, frame_sequence=sequence)
            # Touch replacement storage before losing the wrapper reference.
            self.assertEqual(frame.values.size, 4096)
        del frame
        gc.collect()
        for retained, expected in ((frequencies, expected_frequencies), (values, expected_values)):
            np.testing.assert_array_equal(retained, expected)
            self.assertFalse(retained.flags.writeable)
            with self.assertRaises(ValueError):
                retained[0] = 0
            with self.assertRaises(ValueError):
                retained.setflags(write=True)
