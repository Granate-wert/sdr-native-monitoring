"""Terminal Qt ownership receipts for the real Spectrum and Waterfall panes."""
from __future__ import annotations

import gc
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from sdr_monitor.ui.v2.spectrum.plot_terminal import (
    capture_plot_terminal_ownership,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane


class QtTerminalOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def _owned_pair(self):
        host = QWidget()
        host_layout = QVBoxLayout(host)
        spectrum = SpectrumScene(parent=host)
        waterfall = WaterfallPane(parent=host)
        host_layout.addWidget(spectrum)
        host_layout.addWidget(waterfall)
        return host, host_layout, spectrum, waterfall

    @staticmethod
    def _layout_entries(plot):
        return tuple(
            plot.layout.itemAt(row, column)
            for row in range(plot.layout.rowCount())
            for column in range(plot.layout.columnCount())
            if plot.layout.itemAt(row, column) is not None
        )

    def _finish_widget(self, widget):
        if not shiboken6.isValid(widget):
            return
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)

    def test_real_panes_detach_owned_grid_items_and_registry_before_parent_delete(self):
        host, host_layout, spectrum, waterfall = self._owned_pair()
        plans = tuple(capture_plot_terminal_ownership(pane.plot_item)
                      for pane in (spectrum, waterfall))
        panes = (spectrum, waterfall)
        host_membership = tuple(host_layout.itemAt(index).widget()
                                for index in range(host_layout.count()))
        initial_views = set(pg.ViewBox.AllViews)
        self.assertEqual(host_membership, panes)

        for pane, plan in zip(panes, plans):
            self.assertIn(plan.view_box, initial_views | set(pg.ViewBox.AllViews))
            self.assertIsNotNone(plan.title_label)
            self.assertTrue(shiboken6.isValid(plan.title_label))
            if pane is spectrum:
                pane.release_graphics_after_shutdown()
            else:
                pane.release_presentation_after_shutdown()
            actual_plan = pane._graphics_terminal_ownership
            self.assertIsNotNone(actual_plan)
            self.assertTrue(actual_plan.structural_complete)
            self.assertTrue(pane._graphics_terminal_released)
            self.assertFalse(shiboken6.isValid(plan.view_box))
            self.assertTrue(all(not shiboken6.isValid(axis) for axis in plan.axes))
            self.assertTrue(all(not shiboken6.isValid(label) for label in plan.labels))
            self.assertTrue(shiboken6.isValid(plan.title_label))
            self.assertTrue(set(self._layout_entries(pane.plot_item)) <= {plan.title_label})
            self.assertNotIn(plan.view_box, pg.ViewBox.AllViews)

        self.assertEqual(tuple(host_layout.itemAt(index).widget()
                              for index in range(host_layout.count())), host_membership)
        peer = SpectrumScene(parent=host)
        self.assertIn(peer.view_box, pg.ViewBox.AllViews)
        peer.release_graphics_after_shutdown()
        self.assertNotIn(peer.view_box, pg.ViewBox.AllViews)
        self.assertFalse(shiboken6.isValid(peer.view_box))

        host.deleteLater()
        QCoreApplication.sendPostedEvents(host, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(shiboken6.isValid(host))
        self.assertFalse(shiboken6.isValid(spectrum))
        self.assertFalse(shiboken6.isValid(waterfall))
        self.assertFalse(shiboken6.isValid(plans[0].title_label))
        self.assertFalse(shiboken6.isValid(plans[1].title_label))

    def test_deferred_delete_failure_retries_same_completed_structural_plan(self):
        host, _layout, spectrum, _waterfall = self._owned_pair()
        plan = capture_plot_terminal_ownership(spectrum.plot_item)
        real_send = QCoreApplication.sendPostedEvents
        calls = []

        def fail_once(receiver, event):
            calls.append(receiver)
            if len(calls) == 1:
                raise RuntimeError("injected terminal DeferredDelete failure")
            return real_send(receiver, event)

        try:
            with patch(
                "sdr_monitor.ui.v2.spectrum.plot_terminal.QCoreApplication.sendPostedEvents",
                side_effect=fail_once,
            ):
                with self.assertRaisesRegex(RuntimeError, "DeferredDelete"):
                    spectrum.release_graphics_after_shutdown()
            plan = spectrum._graphics_terminal_ownership
            self.assertIsNotNone(plan)
            self.assertTrue(plan.structural_complete)
            self.assertFalse(spectrum._graphics_terminal_released)
            self.assertIs(spectrum._graphics_terminal_ownership, plan)
            self.assertTrue(shiboken6.isValid(plan.labels[0]))
            spectrum.release_graphics_after_shutdown()
            self.assertTrue(spectrum._graphics_terminal_released)
            self.assertFalse(shiboken6.isValid(plan.view_box))
            self.assertTrue(all(not shiboken6.isValid(item) for item in plan.axes + plan.labels))
        finally:
            self._finish_widget(spectrum)
            self._finish_widget(host)

    def test_foreign_wrapper_is_refused_before_partial_retry_can_launder_ownership(self):
        host, _layout, spectrum, _waterfall = self._owned_pair()
        peer = SpectrumScene(parent=host)
        plan = capture_plot_terminal_ownership(spectrum.plot_item)
        peer_plan = capture_plot_terminal_ownership(peer.plot_item)
        foreign_label = peer_plan.labels[0]
        foreign_plan = replace(plan, labels=(foreign_label,))
        spectrum._graphics_terminal_ownership = foreign_plan
        spectrum._graphics_preflight_complete = True
        try:
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                spectrum.release_graphics_after_shutdown()
            self.assertFalse(spectrum._graphics_terminal_released)
            self.assertTrue(shiboken6.isValid(foreign_label))
            self.assertIs(foreign_label.parentItem(), peer_plan.axes[0])
        finally:
            spectrum._graphics_terminal_ownership = plan
            if shiboken6.isValid(spectrum):
                spectrum.release_graphics_after_shutdown()
            if shiboken6.isValid(peer):
                peer.release_graphics_after_shutdown()
            self._finish_widget(host)

    def test_release_is_terminal_idempotent_for_both_real_panes(self):
        host, _layout, spectrum, waterfall = self._owned_pair()
        spectrum.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()
        spectrum.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()
        self.assertTrue(spectrum._graphics_terminal_released)
        self.assertTrue(waterfall._graphics_terminal_released)
        self._finish_widget(host)


if __name__ == "__main__":
    unittest.main()
