"""Explicit selected compiled core fixtures; NOT physical/owner/paint evidence."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest

import numpy as np

from tests.native_test_dependencies import native_test_dll_directory


class LayerCreationBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            raise unittest.SkipTest("requires explicit matching native path; no fallback")
        path = Path(selected).resolve(strict=True)
        scope = native_test_dll_directory(str(path))
        scope.__enter__()
        cls.addClassCleanup(scope.__exit__, None, None, None)
        spec = importlib.util.spec_from_file_location("_sdr_native", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("explicit native spec unavailable")
        cls.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.native)

    def test_actual_sweep_creation_variants_readonly_no_borrowed_generation(self):
        native = self.native
        self.assertEqual(native.LAYER_CREATION_CONTRACT_VERSION, 1)
        self.assertEqual(native.DENSITY_LAYER_SCALAR_RESERVATION_BYTES, 1024)
        frames = native._make_test_sweep_position_frames(layer_creation=True)
        baseline = native._make_test_sweep_position_frames()
        for index, (frame, control) in enumerate(zip(frames, baseline), 1):
            with self.subTest(index=index):
                ref = frame.layer_ready
                self.assertIsNone(control.layer_ready)
                self.assertIsInstance(ref, native.LayerReadyRef)
                self.assertEqual(ref.creation_sequence, index)
                self.assertEqual(ref.sweep_epoch, frame.epoch)
                self.assertEqual(ref.line_sequence, frame.line_sequence)
                self.assertEqual(ref.config_generation, 0)
                self.assertEqual(ref.clock, native.AnalyticalReadyClock.NativeSteady)
                self.assertEqual(ref.clock_state, native.AnalyticalReadyClockState.Monotonic)
                self.assertEqual(ref.kind, native.LayerReadyKind.SweepProgress if index == 1
                                 else native.LayerReadyKind.SweepTerminal)
                with self.assertRaises(AttributeError):
                    ref.creation_sequence = 100
                np.testing.assert_array_equal(frame.values, control.values)
                np.testing.assert_array_equal(frame.quality_flags_per_bin, control.quality_flags_per_bin)
                np.testing.assert_array_equal(frame.frequencies_hz, control.frequencies_hz)
                if index > 1:
                    self.assertEqual(frame.completed_ns, control.completed_ns)

    def test_actual_density_reset_keeps_data_time_and_distinct_accumulation(self):
        native = self.native
        before = native.analytical_ready_clock_ns()
        first, restarted = native._make_test_density_layer_frames()
        after = native.analytical_ready_clock_ns()
        for frame in (first, restarted):
            ref = frame.layer_ready
            self.assertEqual(ref.kind, native.LayerReadyKind.Density)
            self.assertEqual(ref.config_generation, frame.config_generation)
            self.assertEqual(ref.update_sequence, frame.update_sequence)
            self.assertEqual(ref.source_frame_sequence, frame.source_frame_sequence)
            self.assertEqual(frame.timestamp_ns, 123)
            self.assertLessEqual(before, ref.ready_native_ns)
            self.assertLessEqual(ref.ready_native_ns, after)
            with self.assertRaises(AttributeError):
                ref.accumulation_sequence = 1
        self.assertEqual(first.layer_ready.producer_instance_id, restarted.layer_ready.producer_instance_id)
        self.assertGreater(restarted.layer_ready.accumulation_sequence, first.layer_ready.accumulation_sequence)
        self.assertGreater(restarted.layer_ready.creation_sequence, first.layer_ready.creation_sequence)
        np.testing.assert_array_equal(first.density, restarted.density)
        self.assertEqual(first.probability_scale, restarted.probability_scale)
        self.assertEqual(first.count_scale, restarted.count_scale)

    def test_repeated_binding_reads_are_not_new_creations(self):
        first, _ = self.native._make_test_density_layer_frames()
        original = first.layer_ready
        for _ in range(100):
            reread = first.layer_ready
            self.assertEqual((reread.producer_instance_id, reread.creation_sequence, reread.ready_native_ns),
                             (original.producer_instance_id, original.creation_sequence, original.ready_native_ns))

    def test_independent_fixture_owners_do_not_share_producer(self):
        a = self.native._make_test_sweep_position_frames(layer_creation=True)[0]
        b = self.native._make_test_sweep_position_frames(layer_creation=True)[0]
        density = self.native._make_test_density_layer_frames()[0]
        self.assertEqual(len({a.layer_ready.producer_instance_id, b.layer_ready.producer_instance_id,
                              density.layer_ready.producer_instance_id}), 3)
