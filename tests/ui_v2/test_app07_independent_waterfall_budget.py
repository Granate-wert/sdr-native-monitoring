"""Independent graph panes bind Waterfalls to their one prepared-pane ledger."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import gc
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import weakref
from unittest.mock import patch

import pyqtgraph as pg

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup,
    ReceiverBindingMode,
    ReceiverChainSelection,
    ReceiverEndpoint,
)
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView
from sdr_monitor.ui.v2.waterfall.pane import waterfall_lookup_table
from sdr_monitor.ui.v2.workspaces.analyzer_pane import AnalyzerPaneViewV2
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from tests.test_app07_pane_resource_session import live_frame
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)
from tests.test_app07_shared_capture_schedule import pane as pane_request
from tests.test_app07_shared_capture_schedule import profile


class IndependentWaterfallBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.native, cls.support = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls) -> None:
        restore_explicit_native_support(cls)

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self._boards: list[IndependentPaneBoardV2] = []
        self._preparers: list[PaneDeliveryPreparer] = []

    def tearDown(self) -> None:
        for board in self._boards:
            if not board._terminal_released:
                board.release_presentation_after_shutdown()
            board.close()
            board.deleteLater()
        for preparer in self._preparers:
            preparer.clear()
        self.app.processEvents()
        self.temp.cleanup()

    def _board(self, count: int, *, budget: PresentationAllocationBudget | None = None):
        groups = tuple(
            AcquisitionGroup(
                f"resource-{index}:group",
                f"resource-{index}",
                (ReceiverEndpoint(
                    f"rx-{index}", f"resource-{index}:source", f"resource-{index}",
                    ReceiverChainSelection.RX1,
                ),),
            )
            for index in range(1, count + 1)
        )
        slots = []
        for index in range(1, count + 1):
            start = 100_000_000.0 + (index - 1) * 8_000_000.0
            slots.append(PaneLayoutSlot(
                index,
                pane_request(
                    f"pane-{index}", f"rx-{index}", start, start + 8_000_000.0,
                    ReceiverBindingMode.DEDICATED_PARALLEL,
                ),
            ))
        if count == 3:
            slots.append(PaneLayoutSlot(4))
        layout = compile_pane_layout(
            tuple(slots), groups,
            {"capture": profile(61_440_000.0, CaptureMeasurementMode.RTBW)},
        )
        selected_budget = budget or PresentationAllocationBudget()
        self.assertTrue(selected_budget.install_waterfall_storage_support(self.support))
        preparer = PaneDeliveryPreparer(layout, groups, selected_budget)
        settings = QSettings(
            str(Path(self.temp.name) / f"board-{count}-{len(self._boards)}.ini"),
            QSettings.Format.IniFormat,
        )
        board = IndependentPaneBoardV2(preparer, settings=settings)
        self._preparers.append(preparer)
        self._boards.append(board)
        return board, preparer, selected_budget

    @staticmethod
    def _delivery(preparer: PaneDeliveryPreparer, pane_id: str, sequence: int) -> PaneDelivery:
        binding = preparer.bindings[pane_id]
        index = binding.slot_number
        center = 104_000_000.0 + (index - 1) * 8_000_000.0
        bundle = live_frame(binding.source_id, f"session-{pane_id}", 7, center_hz=center)
        spectrum = replace(bundle.spectrum, sequence=sequence, timestamp_ns=sequence,
                           acquisition_epoch=7)
        bundle = replace(bundle, spectrum=spectrum, identity=None)
        delivery = PaneDelivery(
            binding.physical_stream_resource_id,
            binding.capture_id,
            binding.receiver_endpoint_id,
            1,
            binding.crop,
            bundle,
            float(sequence),
            1,
        )
        return delivery

    @classmethod
    def _prepared(cls, preparer: PaneDeliveryPreparer, pane_id: str, sequence: int):
        return preparer.prepare(cls._delivery(preparer, pane_id, sequence))

    @staticmethod
    def _qimage_bytes(image: QImage) -> bytes:
        return bytes(image.constBits())[:image.sizeInBytes()]

    def _assert_dispatched_native_board(self, count: int) -> None:
        board, preparer, budget = self._board(count)
        board.resize(1400, 900)
        board.show()
        self.app.processEvents()
        for index in range(1, count + 1):
            prepared = self._prepared(preparer, f"pane-{index}", index)
            self.assertIsNone(prepared.bundle.persistence)
            self.assertTrue(board.apply_prepared(prepared))
        self.app.processEvents()
        for pane in board._panes.values():
            pane.waterfall_pane._graphics.viewport().repaint()
        self.app.processEvents()

        rendered = []
        for pane in board._panes.values():
            waterfall = pane.waterfall_pane
            ring = waterfall._renderer.buffer
            self.assertIsNotNone(ring)
            self.assertIs(type(ring._protected_storage), self.native._WaterfallProtectedStorage)
            self.assertEqual(waterfall.history_rows, 1)
            self.assertIs(waterfall._renderer.allocation_budget, budget)
            item = waterfall._image_items[0]
            self.assertIsNotNone(item.qimage)
            self.assertGreater(item.raster_fast_renders, 0)
            rendered.append((waterfall, item))

        # Compare actual Qt-paint images against pyqtgraph's original mapper in
        # a common format; the counter above proves the original dispatch ran.
        rgba8888 = QImage.Format.Format_RGBA8888
        for waterfall, item in rendered:
            actual = item.qimage
            self.assertIsNotNone(item.image)
            generic = pg.ImageItem(axisOrder="row-major")
            generic.setLookupTable(waterfall_lookup_table(waterfall.config.palette))
            generic.setImage(item.image, autoLevels=False,
                             levels=(waterfall.config.level_min, waterfall.config.level_max))
            generic.render()
            expected = generic.qimage
            self.assertIsNotNone(expected)
            actual_rgba = actual.convertToFormat(rgba8888)
            expected_rgba = expected.convertToFormat(rgba8888)
            self.assertEqual((actual_rgba.width(), actual_rgba.height()),
                             (expected_rgba.width(), expected_rgba.height()))
            self.assertEqual(self._qimage_bytes(actual_rgba), self._qimage_bytes(expected_rgba))

    def test_three_and_four_occupied_boards_share_budget_without_projectors(self) -> None:
        for count in (3, 4):
            board, _preparer, budget = self._board(count)
            self.assertEqual(len(board._panes), count)
            self.assertEqual(len(board._empty_slots), 1 if count == 3 else 0)
            self.assertEqual(len(board._display_overlays), count)
            for pane in board._panes.values():
                self.assertIsNone(pane.spectrum_scene._projector)
                self.assertIs(pane.waterfall_pane._renderer.allocation_budget, budget)

    def test_prepared_rows_create_protected_rings_and_match_pg_pixel_oracle(self) -> None:
        self._assert_dispatched_native_board(3)
        self._assert_dispatched_native_board(4)

    def test_explicit_type_and_projector_conflicts_refuse_before_qwidget(self) -> None:
        budget = PresentationAllocationBudget()
        foreign = PresentationAllocationBudget()
        with patch.object(SpectrumWaterfallView, "__init__", autospec=True) as widget_init:
            with self.assertRaises(TypeError):
                AnalyzerPaneViewV2(AnalyzerMode.RTBW, allocation_budget=object())
            widget_init.assert_not_called()

        without_budget = SpectrumProjector(lambda _work: Future())
        with patch.object(SpectrumWaterfallView, "__init__", autospec=True) as widget_init:
            with self.assertRaisesRegex(ValueError, "share one allocation budget"):
                AnalyzerPaneViewV2(
                    AnalyzerMode.RTBW, projector=without_budget, allocation_budget=budget,
                )
            widget_init.assert_not_called()

        foreign_projector = SpectrumProjector(lambda _work: Future(), allocation_budget=foreign)
        with patch.object(SpectrumWaterfallView, "__init__", autospec=True) as widget_init:
            with self.assertRaisesRegex(ValueError, "share one allocation budget"):
                AnalyzerPaneViewV2(
                    AnalyzerMode.RTBW, projector=foreign_projector, allocation_budget=budget,
                )
            widget_init.assert_not_called()

        matching_projector = SpectrumProjector(lambda _work: Future(), allocation_budget=budget)
        matching_pane = AnalyzerPaneViewV2(
            AnalyzerMode.RTBW, projector=matching_projector, allocation_budget=budget,
        )
        self.addCleanup(matching_pane.release_presentation_after_shutdown)
        self.assertIs(matching_pane.spectrum_scene._projector, matching_projector)
        self.assertIs(matching_pane.waterfall_pane._renderer.allocation_budget, budget)

    def test_legacy_projector_clear_stop_terminal_and_old_image_keep_their_ledger(self) -> None:
        budget = PresentationAllocationBudget()
        legacy = AnalyzerPaneViewV2(AnalyzerMode.RTBW)
        self.addCleanup(legacy.release_presentation_after_shutdown)
        self.assertIsNone(legacy.waterfall_pane._renderer.allocation_budget)

        projector = SpectrumProjector(lambda _work: Future(), allocation_budget=budget)
        projector_pane = AnalyzerPaneViewV2(AnalyzerMode.RTBW, projector=projector)
        self.addCleanup(projector_pane.release_presentation_after_shutdown)
        self.assertIs(projector_pane.spectrum_scene._projector, projector)
        self.assertIs(projector_pane.waterfall_pane._renderer.allocation_budget, budget)

        board, preparer, old_budget = self._board(1)
        board.resize(1000, 720)
        board.show()
        self.app.processEvents()
        self.assertTrue(board.apply_prepared(self._prepared(preparer, "pane-1", 1)))
        self.app.processEvents()
        pane = board.pane(1)
        waterfall = pane.waterfall_pane
        waterfall._graphics.viewport().repaint()
        self.app.processEvents()
        item = waterfall._image_items[0]
        self.assertIsNotNone(item.qimage)
        self.assertIs(type(waterfall._renderer.buffer._protected_storage),
                      self.native._WaterfallProtectedStorage)

        ring = waterfall._renderer.buffer
        waterfall.clear_history()
        self.assertIs(waterfall._renderer.buffer, ring)
        self.assertEqual(waterfall.history_rows, 0)
        self.assertIs(waterfall._renderer.allocation_budget, old_budget)
        self.assertTrue(board.apply_prepared(self._prepared(preparer, "pane-1", 2)))
        self.assertEqual(waterfall.history_rows, 1)

        # Pending UI Stop does not clear accepted rows or migrate the ledger.
        board.set_ui_stop_pending("resource-1", True)
        board.set_ui_stop_pending("resource-1", False)
        self.assertEqual(waterfall.history_rows, 1)
        self.assertIs(waterfall._renderer.allocation_budget, old_budget)

        waterfall._graphics.viewport().repaint()
        self.app.processEvents()
        item = waterfall._image_items[0]
        external = QImage(item.qimage)
        plane = waterfall._raster_cache.plane
        self.assertIsNotNone(plane)
        plane_size = plane.nbytes
        plane_ref = weakref.ref(plane)
        del plane
        board.release_presentation_after_shutdown()
        preparer.clear()
        self.assertIs(waterfall._renderer.allocation_budget, old_budget)
        self.assertIsNotNone(plane_ref())
        observed_with_image = old_budget.snapshot().observed_bytes
        self.assertGreaterEqual(observed_with_image, plane_size)

        new_board, _new_preparer, new_budget = self._board(1)
        self.assertIsNot(new_budget, old_budget)
        self.assertIs(new_board.pane(1).waterfall_pane._renderer.allocation_budget, new_budget)
        self.assertGreaterEqual(old_budget.snapshot().observed_bytes, plane_size)

        del external
        del item
        gc.collect()
        self.app.processEvents()
        self.assertIsNone(plane_ref())
        self.assertLessEqual(old_budget.snapshot().observed_bytes,
                             observed_with_image - plane_size)

    def test_shared_budget_refuses_later_distinct_source_without_disturbing_first_pane(self) -> None:
        board, preparer, budget = self._board(2)
        board.resize(1400, 900)
        board.show()
        self.app.processEvents()
        first = preparer.prepare(self._delivery(preparer, "pane-1", 1))
        first_source_copy = first.bundle.spectrum.values.copy()
        self.assertTrue(board.apply_prepared(first))
        first_pane = board.pane(1).waterfall_pane
        self.assertEqual(first_pane.history_rows, 1)
        self.app.processEvents()
        first_pane._graphics.viewport().repaint()
        self.app.processEvents()
        first_item = first_pane._image_items[0]
        first_image = first_item.qimage
        self.assertIsNotNone(first_image)
        self.assertGreater(first_item.raster_fast_renders, 0)
        before_pressure = budget.snapshot()
        self.assertGreater(before_pressure.observed_bytes, 0)
        self.assertEqual(before_pressure.reserved_bytes, 0)

        # First-pane roots consume the available ledger; a distinct pane source
        # is refused before it can alter the accepted history or paint output.
        budget.limit_bytes = before_pressure.observed_bytes
        second_delivery = self._delivery(preparer, "pane-2", 2)
        with self.assertRaisesRegex(ValueError, "shared presentation allocation budget"):
            preparer.prepare(second_delivery)
        self.assertEqual(first_pane.history_rows, 1)
        self.assertIs(first_item.qimage, first_image)
        self.assertTrue((first.bundle.spectrum.values == first_source_copy).all())
        after_pressure = budget.snapshot()
        self.assertEqual(after_pressure.reserved_bytes, 0)
        self.assertGreaterEqual(after_pressure.rejections, before_pressure.rejections + 1)


if __name__ == "__main__":
    unittest.main()
