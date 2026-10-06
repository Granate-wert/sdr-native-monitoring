"""Terminal Qt ownership receipts for the real Spectrum and Waterfall panes."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from sdr_monitor.ui.v2.spectrum.plot_terminal import (
    capture_plot_terminal_ownership,
)
from sdr_monitor.ui.v2.spectrum import plot_terminal
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.waterfall.spectrum_view import SpectrumWaterfallView
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
from sdr_monitor.ui.v2.workspaces.analyzer_rf_controls import AnalyzerRfControls


class QtTerminalOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _owned_pair(self):
        host = QWidget()
        self.addCleanup(self._finish_widget, host)
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
            retained_layout = self._layout_entries(pane.plot_item)
            self.assertEqual(len(retained_layout), 1)
            self.assertEqual(retained_layout, (plan.title_label,))
            self.assertNotIn(plan.view_box, pg.ViewBox.AllViews)

        self.assertEqual(tuple(host_layout.itemAt(index).widget()
                              for index in range(host_layout.count())), host_membership)
        peer = SpectrumScene(parent=host)
        self.addCleanup(self._finish_widget, peer)
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
        plan = capture_plot_terminal_ownership(spectrum.plot_item)
        try:
            failure_axis = plan.axis_labels[2][0]
            with patch.object(
                failure_axis, "close", side_effect=RuntimeError("injected third-axis close failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "third-axis"):
                    spectrum.release_graphics_after_shutdown()
            plan = spectrum._graphics_terminal_ownership
            self.assertIsNotNone(plan)
            self.assertFalse(plan.structural_complete)
            self.assertEqual(sum(axis.label is None for axis, _label in plan.axis_labels), 2)
            self.assertTrue(all(shiboken6.isValid(label)
                               for _axis, label in plan.axis_labels if label is not None))
            cleared_label = plan.axis_labels[0][1]
            self.assertIsNotNone(cleared_label)
            foreign_parent = object()
            original_qobject_parent = plot_terminal._qobject_parent

            def observed_parent(wrapper):
                if wrapper is cleared_label:
                    return foreign_parent
                return original_qobject_parent(wrapper)

            with patch.object(plot_terminal, "_qobject_parent", side_effect=observed_parent):
                with self.assertRaisesRegex(RuntimeError, "foreign"):
                    spectrum.release_graphics_after_shutdown()
                self.assertFalse(spectrum._graphics_terminal_released)
                self.assertTrue(shiboken6.isValid(cleared_label))
        finally:
            spectrum._graphics_terminal_ownership = plan
            if shiboken6.isValid(spectrum):
                spectrum.release_graphics_after_shutdown()
            self._finish_widget(host)

    def test_release_is_terminal_idempotent_for_both_real_panes(self):
        host, _layout, spectrum, waterfall = self._owned_pair()
        spectrum.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()
        spectrum.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()
        self.assertTrue(spectrum._graphics_terminal_released)
        self.assertTrue(waterfall._graphics_terminal_released)

    def test_named_viewbox_is_conservatively_refused_before_structural_close(self):
        host, _layout, spectrum, _waterfall = self._owned_pair()
        view_box = spectrum.view_box
        plan = capture_plot_terminal_ownership(spectrum.plot_item)
        with patch(
            "sdr_monitor.ui.v2.spectrum.plot_terminal._drain_view_box",
            side_effect=RuntimeError("injected structural close failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "structural close"):
                spectrum.release_graphics_after_shutdown()
        self.assertFalse(plan.structural_complete)
        view_box.register("m8-terminal-ownership-test")
        try:
            with self.assertRaisesRegex(RuntimeError, "named terminal ViewBox"):
                spectrum.release_graphics_after_shutdown()
            self.assertFalse(spectrum._graphics_terminal_released)
            self.assertFalse(plan.structural_complete)
        finally:
            if shiboken6.isValid(view_box) and view_box in pg.ViewBox.AllViews:
                view_box.unregister()
            if shiboken6.isValid(view_box):
                view_box.name = None
            if shiboken6.isValid(spectrum):
                spectrum.release_graphics_after_shutdown()

    def test_named_viewbox_is_refused_on_completed_retry_before_unregister(self):
        host, _layout, spectrum, _waterfall = self._owned_pair()
        plan = capture_plot_terminal_ownership(spectrum.plot_item)
        view_box = plan.view_box
        spectrum._graphics_terminal_ownership = plan
        spectrum._graphics_preflight_complete = True
        try:
            with patch.object(
                plot_terminal, "_delete_wrapper",
                side_effect=RuntimeError("injected completed retirement failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "completed retirement"):
                    spectrum.release_graphics_after_shutdown()
            self.assertTrue(plan.structural_complete)
            self.assertFalse(spectrum._graphics_terminal_released)
            view_box.register("m8-terminal-ownership-completed-retry")
            with self.assertRaisesRegex(RuntimeError, "named terminal ViewBox"):
                spectrum.release_graphics_after_shutdown()
            self.assertIn(view_box, pg.ViewBox.AllViews)
            self.assertFalse(spectrum._graphics_terminal_released)
        finally:
            if shiboken6.isValid(view_box) and view_box in pg.ViewBox.AllViews:
                view_box.unregister()
            if shiboken6.isValid(view_box):
                view_box.name = None
            if shiboken6.isValid(spectrum):
                spectrum.release_graphics_after_shutdown()

    def test_nonnull_qobject_parent_is_refused_at_capture(self):
        host, _layout, spectrum, _waterfall = self._owned_pair()
        foreign_parent = object()
        real_parent = plot_terminal._qobject_parent

        def observed_parent(wrapper):
            return foreign_parent if wrapper is spectrum.view_box else real_parent(wrapper)

        with patch.object(plot_terminal, "_qobject_parent", side_effect=observed_parent):
            with self.assertRaisesRegex(RuntimeError, "non-null QObject parent"):
                capture_plot_terminal_ownership(spectrum.plot_item)
        spectrum.release_graphics_after_shutdown()

    def test_simulated_qobject_parent_adoption_is_refused_before_structural_close(self):
        host, _layout, spectrum, _waterfall = self._owned_pair()
        plan = capture_plot_terminal_ownership(spectrum.plot_item)
        target = plan.axis_labels[2][1]
        self.assertIsNotNone(target)
        foreign_parent = object()

        def observed_parent(wrapper):
            return foreign_parent if wrapper is target else None

        spectrum._graphics_terminal_ownership = plan
        spectrum._graphics_preflight_complete = True
        with patch.object(plot_terminal, "_qobject_parent", side_effect=observed_parent):
            with self.assertRaisesRegex(RuntimeError, "foreign QObject parent"):
                spectrum.release_graphics_after_shutdown()
        self.assertFalse(spectrum._graphics_terminal_released)
        self.assertIsNotNone(spectrum.plot_item.ctrlMenu)
        spectrum._graphics_terminal_ownership = plan
        spectrum.release_graphics_after_shutdown()

    def test_viewbox_delete_witness_is_empty_and_waterfall_retry_preserves_metrics(self):
        host, _layout, spectrum, waterfall = self._owned_pair()
        plan = capture_plot_terminal_ownership(waterfall.plot_item)
        pre_retry_metrics = None
        witnessed = []
        original_delete = plot_terminal._delete_wrapper

        def witness(wrapper):
            if hasattr(wrapper, "addedItems") and hasattr(wrapper, "childGroup") and not witnessed:
                self.assertEqual(wrapper.addedItems, [])
                self.assertEqual(wrapper.childGroup.childItems(), [])
                witnessed.append(True)
            return original_delete(wrapper)

        real_send = QCoreApplication.sendPostedEvents
        calls = []

        def fail_once(receiver, event):
            calls.append(receiver)
            if len(calls) == 1:
                raise RuntimeError("injected waterfall DeferredDelete failure")
            return real_send(receiver, event)

        try:
            with patch.object(plot_terminal, "_delete_wrapper", side_effect=witness), patch(
                "sdr_monitor.ui.v2.spectrum.plot_terminal.QCoreApplication.sendPostedEvents",
                side_effect=fail_once,
            ):
                with self.assertRaisesRegex(RuntimeError, "DeferredDelete"):
                    waterfall.release_presentation_after_shutdown()
            pre_retry_metrics = waterfall.metrics
            self.assertFalse(waterfall._graphics_terminal_released)
            waterfall.release_presentation_after_shutdown()
            self.assertTrue(waterfall._graphics_terminal_released)
            self.assertEqual(waterfall.metrics, pre_retry_metrics)
            self.assertEqual(witnessed, [True])
            self.assertFalse(shiboken6.isValid(plan.view_box))
        finally:
            self._finish_widget(host)

    def test_active_cancel_clears_both_real_viewboxes_and_preserves_pending(self):
        view = SpectrumWaterfallView()
        self.addCleanup(self._finish_widget, view)

        class Controller:
            pending = True

            def __init__(self):
                self.cancel_calls = 0

            def cancel(self):
                self.cancel_calls += 1

        controller = Controller()
        controls = AnalyzerRfControls.__new__(AnalyzerRfControls)
        controls._retired = False
        controls.dialog = None
        controls._installed = [view]
        controls.controller = controller
        view.spectrum_scene.view_box._rf_drag = (object(), 1.0, 1.0)
        view.waterfall_pane.view_box._rf_drag = (object(), 1.0, 1.0)

        controls.cancel()

        self.assertIsNone(view.spectrum_scene.view_box._rf_drag)
        self.assertIsNone(view.waterfall_pane.view_box._rf_drag)
        self.assertEqual(controller.cancel_calls, 1)
        self.assertTrue(controller.pending)

    def test_retired_cancel_is_safe_after_terminal_child_release_and_view_close(self):
        view = SpectrumWaterfallView()
        self.addCleanup(self._finish_widget, view)
        controls = AnalyzerRfControls.__new__(AnalyzerRfControls)
        controls._retired = True
        controls.dialog = None
        controls._installed = [view]
        controls.controller = None
        view.waterfall_pane.release_presentation_after_shutdown()
        view.spectrum_scene.release_graphics_after_shutdown()

        controls.cancel()
        view.hide()
        view.close()


if __name__ == "__main__":
    unittest.main()
