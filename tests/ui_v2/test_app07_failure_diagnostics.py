"""Actual V2 three-family composition, finite first-cause UI, fake serial."""

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt

from sdr_monitor.services.pane_resource_diagnostics import (
    PaneFailureReason, PaneFailureStage, PaneResourceFailure,
)
from sdr_monitor.services.tinysa_owned_acquisition import TinySaAcquisitionFailure, TinySaAcquisitionPhase
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaTraceFailureReason
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.workspaces.pane_failure_text import pane_failure_text
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase

from tests.ui_v2 import test_app07_independent_failures as fixture


class PaneFailureUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.IndependentPaneFailureTests.setUpClass()

    def setUp(self):
        self.fixture = fixture.IndependentPaneFailureTests("runTest")
        self.locale = current_locale()
        self.addCleanup(set_active_locale, self.locale)

    def test_every_fixed_stage_and_instrument_code_translates_ru_and_en(self):
        for locale in UiLocale:
            set_active_locale(locale)
            for stage in PaneFailureStage:
                for reason in PaneFailureReason:
                    with self.subTest(locale=locale, stage=stage, reason=reason):
                        value = pane_failure_text(PaneResourceFailure(stage, reason))
                        self.assertNotIn("analyzer.independent.failure.", value)
                        self.assertIn(f"[{stage.value}/{reason.value}]", value)
            for phase in TinySaAcquisitionPhase:
                for reason in TinySaTraceFailureReason:
                    failure = PaneResourceFailure(PaneFailureStage.OWNER_POLL, PaneFailureReason.INSTRUMENT_FAILURE,
                        TinySaAcquisitionFailure(phase, reason))
                    value = pane_failure_text(failure)
                    self.assertNotIn("analyzer.independent.failure.", value)
                    self.assertIn(f"; {phase.value}/{reason.value}]", value)
        with self.assertRaises(TypeError):
            pane_failure_text(RuntimeError("PRIVATE"))

    def test_start_failure_shows_affected_pane_and_plain_text_in_both_locales(self):
        with self.fixture.product(configure_hackrf=lambda hf: setattr(hf.factory, "fail", True)) as product:
            product.ui.start_all.click()
            self.fixture.wait(lambda: self.fixture.phases(product)[1] is PanePumpPhase.STOP_REQUIRED)
            for locale in UiLocale:
                set_active_locale(locale)
                product.ui.set_locale()
                self.assertIn("[start/operation_failed]", product.ui.error.text())
                self.assertIn("2", product.ui.error.text())
                label = product.ui.board._timing_labels[2]
                self.assertIn("[start/operation_failed]", label.toolTip())
                self.assertEqual(label.accessibleDescription(), label.toolTip())
                self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
                self.assertEqual(product.ui.error.textFormat(), Qt.TextFormat.PlainText)
                self.assertNotIn("PRIVATE", product.ui.error.text())
                self.assertNotIn("[start/operation_failed]", product.ui.board._timing_labels[1].toolTip())
            self.fixture.publish_ad(product, 2)
            self.assertEqual(self.fixture.phases(product)[0], PanePumpPhase.RUNNING)
            self.assertEqual(self.fixture.phases(product)[2], PanePumpPhase.RUNNING)

    def test_cleanup_does_not_replace_first_cause_and_successful_stop_keeps_history(self):
        with self.fixture.product() as product:
            product.ui.start_all.click()
            self.fixture.wait(lambda: all(phase is PanePumpPhase.RUNNING for phase in self.fixture.phases(product)))
            control = product.hf.factory.controls[0]
            control.drain_latest_spectrum_frame = lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE wire"))
            self.fixture.wait(lambda: self.fixture.phases(product)[1] is PanePumpPhase.STOP_REQUIRED)
            first = product.handle.pump.snapshot()[1].first_failure
            self.assertEqual(first.stage, PaneFailureStage.OWNER_POLL)
            product.ui.board.select_slot(2)
            control.stop_fail = True
            product.ui.stop_selected.click()
            self.fixture.wait(lambda: product.handle.pump.snapshot()[1].cleanup_failure is not None)
            product.ui._refresh()
            self.assertIs(product.handle.pump.snapshot()[1].first_failure, first)
            self.assertIn("[owner_poll/operation_failed]", product.ui.error.text())
            self.assertIn("[stop/operation_failed]", product.ui.error.text())
            control.stop_fail = False
            product.ui.stop_selected.click()
            self.fixture.wait(lambda: self.fixture.phases(product)[1] is PanePumpPhase.STOPPED)
            product.ui._refresh()
            self.assertEqual(product.ui.error.text(), "")
            self.assertIn("[owner_poll/operation_failed]", product.ui.board._timing_labels[2].toolTip())
            product.ui.start_selected.click()
            self.fixture.wait(lambda: self.fixture.phases(product)[1] is PanePumpPhase.RUNNING)
            self.assertIsNone(product.handle.pump.snapshot()[1].first_failure)
            product.ui._refresh()
            self.assertNotIn("[owner_poll/operation_failed]", product.ui.board._timing_labels[2].toolTip())

    def test_real_fake_serial_framing_cause_reaches_pane_without_extra_io_or_neighbor_stop(self):
        def bad_serial(ts):
            original = ts.provider._acquisition_factory
            def acquisition(*args):
                owner = original(*args)
                factory = owner._factory
                def serial(route):
                    port = factory(route)
                    # Exact expected length but wrong per-point binary delimiter.
                    port.payload = b"{" + b"q\x80\x0c" * 3 + b"}"
                    return port
                owner._factory = serial
                return owner
            ts.provider._acquisition_factory = acquisition
        with self.fixture.product(configure_tinysa=bad_serial) as product:
            product.ui.start_all.click()
            self.fixture.wait(lambda: self.fixture.phases(product)[2] is PanePumpPhase.STOP_REQUIRED)
            failure = product.handle.pump.snapshot()[2].first_failure
            self.assertEqual(failure, PaneResourceFailure(PaneFailureStage.OWNER_POLL,
                PaneFailureReason.INSTRUMENT_FAILURE,
                TinySaAcquisitionFailure(TinySaAcquisitionPhase.SCAN, TinySaTraceFailureReason.FRAMING)))
            product.ui._refresh()
            self.assertIn("scan/framing]", product.ui.error.text())
            self.assertIs(failure.instrument, product.ts.instrument.acquisition_failure)
            port = product.ts.serials[0]
            calls = list(port.calls)
            for _ in range(10):
                product.ui._refresh()
                product.ts.instrument.poll_latest()
                self.assertIs(product.ts.instrument.acquisition_failure, failure.instrument)
            self.assertEqual(port.calls, calls)
            self.assertEqual(len(product.ts.serials), 1)
            self.fixture.publish_ad(product, 3)
            self.assertEqual(self.fixture.phases(product)[:2], (PanePumpPhase.RUNNING,) * 2)
            product.ui.board.select_slot(3)
            product.ui.stop_selected.click()
            self.fixture.wait(lambda: self.fixture.phases(product)[2] is PanePumpPhase.STOPPED)
            self.assertIs(product.ts.instrument.acquisition_failure, failure.instrument)

    def test_fixed_label_does_not_relayout_on_cached_failure_refresh(self):
        with self.fixture.product(configure_hackrf=lambda hf: setattr(hf.factory, "fail", True)) as product:
            product.ui.start_all.click()
            self.fixture.wait(lambda: self.fixture.phases(product)[1] is PanePumpPhase.STOP_REQUIRED)
            product.ui._refresh()
            label = product.ui.board._timing_labels[2]
            geometry = label.geometry()
            with patch.object(label, "setText", wraps=label.setText) as set_text, \
                    patch.object(label, "setToolTip", wraps=label.setToolTip) as set_tooltip:
                for _ in range(10):
                    product.ui._refresh()
                set_text.assert_not_called()
                set_tooltip.assert_not_called()
            self.assertEqual(label.geometry(), geometry)
