"""The opt-in substage observer must be bounded and retain scalar evidence only."""

import unittest
import weakref
from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
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
    def test_required_pending_overlap_counts_only_admitted_scalar_offers(self):
        entered, release = Event(), Event()
        request = PersistenceImageRequest(view(np.full((4, 16), .4, np.float32)),
                                          PersistenceImagePolicy(1, PersistenceRenderMode.DIRECT, False))

        def held_prepare(work, *args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("observer fixture was not released")
            return projection.prepare_persistence_image(work, *args, **kwargs)

        class FakeProjector:
            def __init__(self):
                self._pending = None

            def offer(self, work):
                if work.accepted:
                    self._pending = work

        class FakeLivePresenter:
            def __init__(self):
                self._projection_in_flight = True
                self._pending_preparation = None
                self._pending_commands = 0
                self._preparation_future = None

            def _offer_preparation(self, snapshot, revision, *, render=True):
                if render:
                    self._pending_preparation = (snapshot, revision, render)

        fake_module = SimpleNamespace(prepare_persistence_image=held_prepare,
                                      SpectrumProjector=FakeProjector)
        original_offer = FakeProjector.offer
        recorder = SubstageRecorder()
        with recorder.instrument(persistence_projection, fake_module,
                                 live_presenter_class=FakeLivePresenter):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fake_module.prepare_persistence_image, request)
                try:
                    self.assertTrue(entered.wait(2))
                    projector = FakeProjector()
                    projector.offer(SimpleNamespace(required_work=True, accepted=False))
                    projector.offer(SimpleNamespace(required_work=True, accepted=True))
                    projector.offer(SimpleNamespace(required_work=False, accepted=True))
                    commanded = FakeLivePresenter()
                    commanded._pending_commands = 1
                    commanded._offer_preparation(object(), 1)
                    preparing = FakeLivePresenter()
                    preparing._preparation_future = object()
                    preparing._offer_preparation(object(), 1)
                    inactive = FakeLivePresenter()
                    inactive._projection_in_flight = False
                    inactive._offer_preparation(object(), 1)
                    FakeLivePresenter()._offer_preparation(object(), 1, render=False)
                    live = FakeLivePresenter()
                    live._offer_preparation(object(), 1)
                    # A later clear does not turn offer-to-end into queue
                    # residence time; the observer must label it honestly.
                    live._pending_preparation = None
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=2).image.shape, (4, 16))
        self.assertIs(FakeProjector.offer, original_offer)
        report = recorder.report()
        self.assertTrue(report["complete"])
        self.assertEqual(report["concurrent_optional"], 0)
        self.assertEqual(report["stages"]["direct"]["jobs_with_required_pending"], 1)
        self.assertEqual(report["stages"]["direct"]["required_pending_offers"], 1)
        self.assertEqual(report["stages"]["direct"]["jobs_with_live_pending"], 1)
        self.assertEqual(report["stages"]["direct"]["live_pending_offers"], 1)
        self.assertGreater(report["records"][0]["required_offer_to_optional_end_ms"], 0)
        self.assertGreater(report["records"][0]["live_offer_to_optional_end_ms"], 0)
        self.assertFalse(any(isinstance(value, np.ndarray)
                             for row in report["records"] for value in row.values()))

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

    def test_record_when_excludes_warmup_without_skipping_worker_result(self):
        request = PersistenceImageRequest(view(np.full((4, 16), .4, np.float32)),
                                          PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False))
        measured = False
        recorder = SubstageRecorder()
        with recorder.instrument(persistence_projection, projection,
                                 record_when=lambda: measured):
            warmup = projection.prepare_persistence_image(request)
            measured = True
            observed = projection.prepare_persistence_image(request)
            measured = False
            after = projection.prepare_persistence_image(request)
        self.assertTrue(np.array_equal(warmup.image, observed.image))
        self.assertTrue(np.array_equal(observed.image, after.image))
        report = recorder.report()
        self.assertEqual((report["total"], report["completed"], report["errors"]), (1, 1, 0))
        self.assertTrue(report["complete"])

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
