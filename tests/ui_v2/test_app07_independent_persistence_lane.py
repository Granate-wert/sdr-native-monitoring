"""Density-only independent wiring on real Qt scenes; no physical RF evidence."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.product_live import V2LiveProductComposition as ProductLiveComposition, compose_v2_live_product
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.persistence_projection import prepare_persistence_image
from sdr_monitor.ui.v2.spectrum.persistence_projector import PersistenceDelivery, PersistenceProjector
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.workspaces.independent_pane_persistence import IndependentPanePersistenceLanes
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from tests.ui_v2 import test_independent_pane_freshness as freshness_fixture
from tests.ui_v2 import test_live_product_composition as composition_fixture
from tests.ui_v2.test_app05_persistence_worker import view
from tests.ui_v2.test_app05_viewport_projection import DisplayFrame, ManualWorker


class LaneFixture:
    """Reuse the qualified three-binding/paired-resource real board fixture.

    The minimal typed handle is ONLY a lane admission unit double; it owns no
    pump/SDK. Full session/Stage/app integration is separately covered below
    and by the root suite, not inferred from this unit double.
    """
    def __init__(self):
        self.base = freshness_fixture.IndependentPaneFreshnessTests()
        self.base.setUp()
        self.worker = ManualWorker()
        self.handle = PaneProductSessionHandle.__new__(PaneProductSessionHandle)
        self.handle._applied = True
        self.handle.preparer = self.base.preparer
        self.handle.layout = self.base.preparer.layout
        self.budget = self.base.preparer.allocation_budget
        self.lanes = IndependentPanePersistenceLanes(self.handle,
            submit=self.worker.submit, allocation_budget=self.budget)
        for pane_id, binding in self.base.preparer.bindings.items():
            self.lanes.attach(pane_id, self.base.board.pane(binding.slot_number).spectrum_scene)

    def scene(self, pane_id="left"):
        binding = self.base.preparer.bindings[pane_id]
        return self.base.board.pane(binding.slot_number).spectrum_scene

    def drain(self, app):
        for _ in range(32):
            app.processEvents()
            if not self.worker.jobs:
                return
            self.worker.finish()
        raise AssertionError("bounded manual jobs did not settle")

    def close(self, app):
        self.lanes.quiesce()
        self.drain(app)
        self.lanes.release_after_shutdown()
        self.base.tearDown()


class IndependentPersistenceLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = LaneFixture()

    def tearDown(self):
        self.fixture.close(self.app)
        self.assertEqual(self.fixture.budget.snapshot().reserved_bytes, 0)

    def density(self, values=None, pane_id="left", mode=DensityValueMode.PROBABILITY):
        frame = view(np.full((256, 4096), .25, np.float32) if values is None else values, mode)
        scene = self.fixture.scene(pane_id)
        scene._persistence._last_upload_ns = None
        scene.set_persistence_frame(frame.source_frame)
        return frame

    def test_three_distinct_ports_same_actual_ledger_and_required_spectrum_direct(self):
        ports = []
        for pane_id in self.fixture.handle.preparer.bindings:
            scene = self.fixture.scene(pane_id)
            port = scene._density_port
            ports.append(port)
            self.assertIsNone(scene._projector)
            self.assertIs(port.allocation_budget, self.fixture.budget)
            self.assertIs(scene._persistence.allocation_budget, self.fixture.budget)
            frame = DisplayFrame(np.linspace(100e6, 101e6, 16), np.full(16, -70., np.float32), "dBm")
            frame.frequencies_hz.setflags(write=False)
            frame.values.setflags(write=False)
            scene.set_frame(frame, prepared=PreparedSpectrumFrame(frame))
            self.assertIs(scene.displayed_frame, frame)
            self.density(pane_id=pane_id)
        self.assertEqual(len({id(port) for port in ports}), 3)
        self.assertEqual(len(self.fixture.worker.jobs), 3)
        self.fixture.drain(self.app)
        self.assertTrue(all(port.completed == 1 for port in ports))

    def test_full_matrix_count_labels_and_exact_uploaded_origin_no_gui_mapping(self):
        scene = self.fixture.scene()
        with patch.object(scene._persistence, "_render_image", side_effect=AssertionError("GUI mapping")):
            frame = self.density(np.full((256, 4096), 12., np.float32), mode=DensityValueMode.COUNT)
            request = scene._density_port._active.request
            expected = prepare_persistence_image(request)
            self.assertIsNone(scene._persistence.image_item.image)
            self.fixture.drain(self.app)
            np.testing.assert_array_equal(scene._persistence.image_item.image.view(np.uint32),
                                          expected.image.view(np.uint32))
            self.assertIs(scene._persistence._uploaded_density, frame.density)
            self.assertEqual(expected.quantitative_labels, ("0 count", "12 count"))
            self.assertEqual(scene.persistence_metrics.image_uploads, 1)

    def test_worker_bitexact_modes_log_count_nan_and_strides(self):
        scene = self.fixture.scene()
        for rendering in PersistenceRenderMode:
            for logarithmic in (False, True):
                for value_mode in DensityValueMode:
                    for stride in (False, True):
                        with self.subTest(rendering=rendering, log=logarithmic, value=value_mode, stride=stride):
                            scene.clear_persistence_display()
                            scene.set_persistence_render_mode(rendering)
                            scene.set_persistence_logarithmic(logarithmic)
                            values = np.full((4, 32), .3 if value_mode is DensityValueMode.PROBABILITY else 8., np.float32)
                            values[0, :4] = (np.nan, np.inf, -np.inf, -0.)
                            if stride:
                                values = values[:, ::-2]
                            self.density(values, mode=value_mode)
                            expected = prepare_persistence_image(scene._density_port._active.request)
                            self.fixture.drain(self.app)
                            actual = scene._persistence.image_item.image
                            np.testing.assert_array_equal(actual.view(np.uint32), expected.image.view(np.uint32))
                            self.assertFalse(actual.flags.writeable)

    def test_injection_once_exclusive_and_before_density_even_after_clear(self):
        scene = self.fixture.scene()
        with self.assertRaises(RuntimeError):
            scene.set_projection_port(SimpleNamespace())
        second = PersistenceProjector(self.fixture.worker.submit, allocation_budget=self.fixture.budget)
        with self.assertRaises(RuntimeError):
            scene.set_persistence_projection_port(second)
        second.dispose()
        second.release_after_shutdown()
        direct = SpectrumScene()
        third = PersistenceProjector(self.fixture.worker.submit, allocation_budget=self.fixture.budget)
        try:
            direct.set_persistence_frame(view(np.full((2, 2), .2, np.float32)).source_frame)
            direct.clear_persistence_display()
            with self.assertRaises(RuntimeError):
                direct.set_persistence_projection_port(third)
        finally:
            third.dispose()
            third.release_after_shutdown()
            direct.release_graphics_after_shutdown()
            direct.close()

    def test_same_port_and_unregistered_pane_refused(self):
        scene = SpectrumScene()
        try:
            with self.assertRaises(ValueError):
                scene.set_persistence_projection_port(self.fixture.scene()._density_port)
            with self.assertRaises(ValueError):
                self.fixture.lanes.attach("foreign", scene)
            with self.assertRaises(ValueError):
                self.fixture.lanes.attach("left", scene)
        finally:
            scene.release_graphics_after_shutdown()
            scene.close()

    def test_foreign_ledger_refuses_before_port_construction(self):
        with patch("sdr_monitor.ui.v2.workspaces.independent_pane_persistence.PersistenceProjector",
                   side_effect=AssertionError("partial construction")):
            with self.assertRaises(ValueError):
                IndependentPanePersistenceLanes(self.fixture.handle, submit=self.fixture.worker.submit,
                    allocation_budget=PresentationAllocationBudget())

    def test_foreign_owner_and_result_identity_cannot_upload(self):
        scene = self.fixture.scene()
        self.density()
        request = scene._density_port._active.request
        expected = prepare_persistence_image(request)
        scene._persistence_projection_ready(PersistenceDelivery(object(), request, expected))
        scene._persistence_projection_ready(PersistenceDelivery(scene._projection_owner,
            replace(request, rematerialize=True), expected))
        self.assertEqual(scene.persistence_metrics.image_uploads, 0)
        self.fixture.drain(self.app)
        self.assertEqual(scene.persistence_metrics.image_uploads, 1)

    def test_shared_stop_preserves_pixels_history_and_unaffected_peer(self):
        for name in ("left", "right", "peer-pane"):
            self.fixture.scene(name).set_persistence_render_mode(PersistenceRenderMode.VISUAL)
            self.density(pane_id=name)
        self.fixture.drain(self.app)
        old = {name: self.fixture.scene(name)._persistence.image_item.image
               for name in ("left", "right", "peer-pane")}
        histories = {name: self.fixture.scene(name)._persistence._worker_history for name in old}
        for name in old:
            self.density(np.full((256, 4096), .8, np.float32), pane_id=name)
        self.fixture.base.board.set_ui_stop_pending("paired", True)
        self.fixture.drain(self.app)
        for name in ("left", "right"):
            scene = self.fixture.scene(name)
            self.assertIs(scene._persistence.image_item.image, old[name])
            self.assertIs(scene._persistence._worker_history, histories[name])
            self.assertIsNone(scene._persistence.worker_request)
            self.assertIsNone(scene._density_port._future)
        self.assertIsNot(self.fixture.scene("peer-pane")._persistence.image_item.image, old["peer-pane"])
        self.fixture.base.board.set_ui_stop_pending("paired", False)
        self.fixture.drain(self.app)
        self.assertIsNot(self.fixture.scene()._persistence.image_item.image, old["left"])

    def test_clear_epoch_gap_boundary_rejects_done_but_unacknowledged_old_density(self):
        scene = self.fixture.scene()
        first = self.density()
        self.fixture.worker.finish()  # Done old result, not GUI acknowledged.
        scene.clear_measurement()  # The actual pane epoch/gap path uses this API.
        next_frame = self.density(np.full((256, 4096), .9, np.float32))
        self.fixture.drain(self.app)
        self.assertIs(scene._persistence._uploaded_density, next_frame.density)
        self.assertIsNot(scene._persistence._uploaded_density, first.density)
        self.assertEqual(scene.persistence_metrics.image_uploads, 1)

    def test_original_start_to_start_cadence_and_one_latest_slot(self):
        scene = self.fixture.scene()
        first = self.density()
        for value in (.4, .5, .6):
            source = view(np.full((256, 4096), value, np.float32))
            scene.set_persistence_frame(source.source_frame)
        self.assertEqual(len(self.fixture.worker.jobs), 1)
        self.assertIs(scene._density_port._active.request.view.density, first.density)
        self.assertIs(scene._persistence.pending_view.density, source.density)
        started_ns = scene._persistence._worker_request_ns
        self.fixture.worker.finish()
        self.app.processEvents()
        self.assertEqual(scene._persistence._last_upload_ns, started_ns)

    def test_composition_missing_submit_and_foreign_ledger_refuse_before_widget(self):
        owner = ProductLiveComposition.__new__(ProductLiveComposition)
        owner._pane_handle = None
        owner._failed_independent_density_lanes = None
        owner._is_shutdown = owner._presentation_disposed = False
        owner.analyzer_view_model = object()
        owner.allocation_budget = self.fixture.budget
        owner._independent_persistence_submit = None
        with patch.object(ProductLiveComposition, "can_close", return_value=True):
            with self.assertRaises(RuntimeError):
                owner.install_independent_pane_session(self.fixture.handle)
            owner.allocation_budget = PresentationAllocationBudget()
            with self.assertRaises(ValueError):
                owner.install_independent_pane_session(self.fixture.handle)
        self.assertIsNone(owner._pane_handle)

    def test_independent_submit_keyword_does_not_change_original_full_projector(self):
        presenter = composition_fixture.FakePresenter()
        composition = compose_v2_live_product(presenter, projection_submit=self.fixture.worker.submit,
            independent_persistence_submit=self.fixture.worker.submit)
        try:
            self.assertIsNone(composition._persistence_submit)
            self.assertIsNone(composition.spectrum_projector.persistence_projector)
            self.assertIsNotNone(composition._independent_persistence_submit)
        finally:
            composition.shutdown()
