"""The opt-in substage observer must be bounded and retain scalar evidence only."""

import unittest
import weakref
from concurrent.futures import CancelledError
from unittest.mock import patch

import numpy as np

from scripts.profile_app05_visual_substages import SubstageRecorder
from sdr_monitor.ui.v2.spectrum import persistence_projection, projection
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy,
    PersistenceImageRequest,
)
from tests.ui_v2.test_app05_persistence_worker import view


class SubstageRecorderTests(unittest.TestCase):
    def test_real_direct_and_visual_calls_are_scalar_only(self):
        direct = PersistenceImageRequest(view(np.full((4, 16), .4, np.float32)),
                                         PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, False))
        visual_policy = PersistenceImagePolicy(2, PersistenceRenderMode.VISUAL, False)
        prior = projection.prepare_persistence_image(PersistenceImageRequest(
            view(np.full((4, 16), .2, np.float32)), visual_policy)).as_history(1)
        visual = PersistenceImageRequest(view(np.full((4, 16), .8, np.float32)),
                                         visual_policy, prior)
        source_ref = weakref.ref(visual.view.density)
        recorder = SubstageRecorder()
        with recorder.instrument(persistence_projection, projection):
            direct_result = projection.prepare_persistence_image(direct)
            visual_result = projection.prepare_persistence_image(visual)
        self.assertEqual(direct_result.image.shape, (4, 16))
        self.assertEqual(visual_result.image.shape, (4, 16))
        result = recorder.report()
        self.assertTrue(result["complete"])
        self.assertEqual((result["total"], result["retained"], result["dropped"]), (2, 2, 0))
        self.assertEqual(result["stages"]["direct"]["calls"]["witness_calls"], 0)
        self.assertEqual(result["stages"]["visual"]["calls"]["witness_calls"], 1)
        self.assertGreater(result["stages"]["visual"]["calls"]["mapping_calls"], 0)
        self.assertEqual((result["completed"], result["cancelled"], result["errors"]), (2, 0, 0))
        self.assertTrue(all(row["other_ms"] >= 0 for row in result["records"]))
        self.assertFalse(any(isinstance(value, np.ndarray)
                             for row in result["records"] for value in row.values()))
        del direct, visual, direct_result, visual_result, prior
        self.assertIsNone(source_ref())

    def test_overflow_invalidates_percentiles_instead_of_claiming_complete_profile(self):
        recorder = SubstageRecorder(capacity=1)
        with recorder.instrument(persistence_projection, projection):
            for _ in range(2):
                projection.prepare_persistence_image(PersistenceImageRequest(
                    view(np.full((4, 16), .4, np.float32)),
                    PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, False)))
        report = recorder.report()
        self.assertEqual((report["total"], report["retained"], report["dropped"]), (2, 1, 1))
        self.assertFalse(report["complete"])
        self.assertIsNone(report["stages"]["direct"]["distributions"]["total_ms"])

    def test_invalid_capacity_is_rejected(self):
        for value in (0, True, 8193):
            with self.assertRaises(ValueError):
                SubstageRecorder(value)

    def test_failure_marks_sample_incomplete_and_restores_all_patches(self):
        request = PersistenceImageRequest(view(np.full((4, 16), .4, np.float32)),
                                          PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, False))
        prior_witness = persistence_projection.persistence_input_witness
        prior_mapping = persistence_projection.map_density_row_for_display
        prior_kernel = persistence_projection._visual_smoothing_kernel
        original_prepare = projection.prepare_persistence_image
        recorder = SubstageRecorder()
        with patch.object(projection, "prepare_persistence_image", side_effect=ValueError("injected")):
            with self.assertRaisesRegex(ValueError, "injected"):
                with recorder.instrument(persistence_projection, projection):
                    projection.prepare_persistence_image(request)
        self.assertIs(projection.prepare_persistence_image, original_prepare)
        self.assertIs(persistence_projection.persistence_input_witness, prior_witness)
        self.assertIs(persistence_projection.map_density_row_for_display, prior_mapping)
        self.assertIs(persistence_projection._visual_smoothing_kernel, prior_kernel)
        self.assertEqual(recorder.report()["total"], 1)
        self.assertFalse(recorder.report()["complete"])
        self.assertEqual(recorder.report()["errors"], 1)

    def test_expected_cancellation_is_counted_not_timed_as_completed(self):
        request = PersistenceImageRequest(view(np.full((4, 16), .4, np.float32)),
                                          PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, False))
        original_prepare = projection.prepare_persistence_image
        calls = 0

        def sometimes_cancel(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise CancelledError("obsolete spectrum presentation")
            return original_prepare(*args, **kwargs)

        recorder = SubstageRecorder()
        with patch.object(projection, "prepare_persistence_image", side_effect=sometimes_cancel):
            with recorder.instrument(persistence_projection, projection):
                with self.assertRaises(CancelledError):
                    projection.prepare_persistence_image(request)
                projection.prepare_persistence_image(request)
        report = recorder.report()
        self.assertTrue(report["complete"])
        self.assertEqual((report["completed"], report["cancelled"], report["errors"]), (1, 1, 0))
        self.assertEqual(report["stages"]["direct"]["distributions"]["total_ms"]["count"], 1)

    def test_composes_with_outer_stage_wrapper(self):
        request = PersistenceImageRequest(view(np.full((4, 16), .4, np.float32)),
                                          PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, False))
        original_prepare = projection.prepare_persistence_image
        calls = []

        def outer(*args, **kwargs):
            calls.append(1)
            return original_prepare(*args, **kwargs)

        recorder = SubstageRecorder()
        with patch.object(projection, "prepare_persistence_image", side_effect=outer):
            with recorder.instrument(persistence_projection, projection):
                projection.prepare_persistence_image(request)
        self.assertEqual(calls, [1])
        self.assertTrue(recorder.report()["complete"])
        self.assertIs(projection.prepare_persistence_image, original_prepare)


if __name__ == "__main__":
    unittest.main()
