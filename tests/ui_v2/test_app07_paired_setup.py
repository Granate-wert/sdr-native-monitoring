"""V2 paired assignment is typed, advisory before Stage, and safe in both locales."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import os
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QApplication, QComboBox

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import (
    CapabilityEvidence, CapabilityEvidenceOrigin, CapabilityField, DeviceCalibrationIdentity,
    DeviceCapabilityBinding, DeviceFamily,
)
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_user_plan import (
    PanePairedSelectionReceipt, PaneSlotDraft, compile_user_pane_plan,
)
from sdr_monitor.ui.v2_pane_user_stage import PaneUserStageError
from tests.test_app07_paired_sweep_contract import request_fixture


def _choice(count: int | None) -> tuple[AnalyzerSourceChoice, object]:
    selected = request_fixture().selected_snapshot
    device = selected.device
    assert device is not None and device.capability_snapshot is not None
    original = device.capability_snapshot
    evidence = tuple(item for item in original.evidence
                     if item.field not in {CapabilityField.RX_CHANNEL_COUNT, CapabilityField.SHARED_RX_LO})
    if count is not None:
        evidence += (CapabilityEvidence(CapabilityField.RX_CHANNEL_COUNT,
                     CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY, "mock-observed-scan-pairs"),)
    snapshot = replace(original, rx_channel_count=count, shared_rx_lo=None, evidence=evidence)
    identity = DeviceCalibrationIdentity(DeviceFamily.AD936X, snapshot.adapter_id,
                                         snapshot.identity_key, "sha256:" + "2" * 64)
    choice = AnalyzerSourceChoice(DeviceCapabilityBinding(device.device_id, DeviceFamily.AD936X,
                                  snapshot.adapter_id, snapshot, identity), None, "Observed AD", "USB")
    return choice, replace(selected, device=replace(device, capability_snapshot=snapshot))


def _enabled(combo: QComboBox, index: int) -> bool:
    model = combo.model()
    assert isinstance(model, QStandardItemModel)
    item = model.item(index)
    assert item is not None
    return item.isEnabled()


class PairedSetupTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))

    def setUp(self) -> None:
        self.locale = current_locale()
        set_active_locale(UiLocale.EN)

    def tearDown(self) -> None:
        set_active_locale(self.locale)

    @staticmethod
    def _editor(choice: AnalyzerSourceChoice) -> IndependentPaneSetupV2:
        editor = IndependentPaneSetupV2(install=lambda _handle: None, uninstall=lambda: None)
        editor.update_sources(AnalyzerSourceSelection(revision=17, choices=(choice,)))
        return editor

    def test_typed_candidate_and_explicit_correction_without_silent_rebind(self) -> None:
        dual, _snapshot = _choice(2)
        editor = self._editor(dual)
        try:
            first, second = editor._rows[:2]
            for row in (first, second):
                row.source.setCurrentIndex(row.source.findData(dual.device_id))
            rx2 = second.chain.findData(ReceiverChainSelection.RX2.value)
            self.assertTrue(_enabled(second.chain, rx2))
            self.assertIn("candidate", second.chain.toolTip())
            second.chain.setCurrentIndex(rx2)
            drafts = editor._read_drafts()
            self.assertIs(drafts[0].receiver_selection, ReceiverChainSelection.RX1)
            self.assertIs(drafts[1].receiver_selection, ReceiverChainSelection.RX2)
            self.assertEqual(second.source_chain.layout().count(), 2)  # Still seven grid columns.
            with patch.object(editor, "_submit") as submit:
                editor._begin_prepare()
                self.assertIs(submit.call_args.args[2][1].receiver_selection,
                              ReceiverChainSelection.RX2)
            for locale in (UiLocale.RU, UiLocale.EN):
                set_active_locale(locale)
                editor.set_locale()
                self.assertIs(editor._read_drafts()[1].receiver_selection, ReceiverChainSelection.RX2)
            single, _ = _choice(1)
            editor.update_sources(AnalyzerSourceSelection(revision=18, choices=(single,)))
            self.assertIs(editor._read_drafts()[1].receiver_selection, ReceiverChainSelection.RX2)
            self.assertFalse(_enabled(second.chain, rx2))
            self.assertTrue(second.chain.isEnabled())  # Correctable even while RX2 is disabled.
            self.assertFalse(editor.prepare.isEnabled())
            with patch.object(editor, "_submit") as submit:
                editor._begin_prepare()
                submit.assert_not_called()
            self.assertIn("one digital I/Q pair", editor.error.text())
            second.chain.setCurrentIndex(second.chain.findData(ReceiverChainSelection.RX1.value))
            self.assertTrue(editor.prepare.isEnabled())
            second.chain.setCurrentIndex(rx2)
            second.source.setCurrentIndex(0)  # Explicit Empty clears this draft, not a refresh.
            self.assertIs(editor._read_drafts()[1].receiver_selection, ReceiverChainSelection.RX1)
            self.assertIsNone(editor._read_drafts()[1].source_id)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_unknown_topology_is_not_guessed_from_ad_name(self) -> None:
        unknown, _ = _choice(None)
        editor = self._editor(unknown)
        try:
            row = editor._rows[0]
            row.source.setCurrentIndex(row.source.findData(unknown.device_id))
            rx2 = row.chain.findData(ReceiverChainSelection.RX2.value)
            self.assertFalse(_enabled(row.chain, rx2))
            self.assertIn("unknown", row.chain.toolTip())
            self.assertIs(editor._read_drafts()[0].receiver_selection, ReceiverChainSelection.RX1)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_preview_has_exact_pair_assignment_and_requested_only_common_profile(self) -> None:
        choice, snapshot = _choice(2)
        receipt = PanePairedSelectionReceipt(choice, 17, snapshot)
        drafts = (PaneSlotDraft(1, choice.device_id, 100e6, 104e6),
                  PaneSlotDraft(2, choice.device_id, 104e6, 108e6,
                                receiver_selection=ReceiverChainSelection.RX2),
                  PaneSlotDraft(3), PaneSlotDraft(4))
        plan = compile_user_pane_plan(drafts, {choice.device_id: choice},
                                      {choice.device_id: 17}, paired_selections={choice.device_id: receipt})
        preview = SimpleNamespace(physical_stream_resource_id="pane-resource-1",
                                  affected_pane_ids=("pane-1", "pane-2"), capture_job_count=1,
                                  recording_conflict=False, revisit_estimates=())
        editor = self._editor(choice)
        try:
            editor._prepared = SimpleNamespace(plan=plan, preview=(preview,),
                                               handle=SimpleNamespace(source_labels={choice.device_id: choice.label}))
            for locale, first, second, filter_text in (
                    (UiLocale.EN, "pane 1 → RX1", "pane 2 → RX2", "RF filter 10 MHz"),
                    (UiLocale.RU, "окно 1 → RX1", "окно 2 → RX2", "RF-фильтр 10 МГц")):
                set_active_locale(locale)
                editor.set_locale()
                result = editor.preview.text()
                self.assertIn(first, result)
                self.assertIn(second, result)
                self.assertIn(filter_text, result)
                self.assertIn("FFT 4096", result)
                self.assertIn("RX", result)
                self.assertIn("100", result)
                self.assertEqual(editor.preview.accessibleName(), result)
            self.assertIn("неизвестны", editor.preview.text())
            resource_id, configuration = plan.initial_ad_configurations[0]
            editor._prepared.plan = replace(plan, initial_ad_configurations=(
                (resource_id, replace(configuration, analog_bandwidth_hz=None)),))
            for locale, filter_text in ((UiLocale.EN, "RF filter unknown ·"),
                                        (UiLocale.RU, "RF-фильтр неизвестен ·")):
                set_active_locale(locale)
                editor.set_locale()
                self.assertIn(filter_text, editor.preview.text())
        finally:
            editor._prepared = None
            editor.release_after_shutdown()
            editor.close()

    def test_paired_sweep_intent_is_never_coerced_to_rtbw(self) -> None:
        choice, _ = _choice(2)
        editor = self._editor(choice)
        try:
            for row in editor._rows[:2]:
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
            editor._rows[1].chain.setCurrentIndex(
                editor._rows[1].chain.findData(ReceiverChainSelection.RX2.value))
            editor._rows[0].mode.setCurrentIndex(
                editor._rows[0].mode.findData(CaptureMeasurementMode.SWEEP.value))
            self.assertIs(editor._read_drafts()[0].measurement_mode, CaptureMeasurementMode.SWEEP)
            self.assertIs(editor._read_drafts()[1].receiver_selection, ReceiverChainSelection.RX2)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_typed_deadline_refusal_preserves_inert_stage_assurance_in_both_locales(self) -> None:
        key = "analyzer.pane.setup.refusal.revisit_infeasible"
        english = text(key, UiLocale.EN)
        russian = text(key, UiLocale.RU)
        for phrase in ("modeled revisit", "target", "weights/ranges",
                       "timing model", "not an RF measurement", "no Apply or RX occurred"):
            self.assertIn(phrase, english)
        for phrase in ("расчётный период возврата", "целевой период", "веса/диапазоны",
                       "расчётная модель", "не RF-измерение", "настройки не применены",
                       "приём не запущен"):
            self.assertIn(phrase, russian)

    def test_all_typed_refusals_are_translated_and_cleanup_takes_precedence(self) -> None:
        choice, _ = _choice(2)
        editor = self._editor(choice)
        try:
            for locale in (UiLocale.EN, UiLocale.RU):
                set_active_locale(locale)
                editor.set_locale()
                for reason in PaneUserRefusal:
                    translated = text(editor._refusal_key(reason))
                    self.assertTrue(translated)
                    self.assertNotEqual(translated, editor._refusal_key(reason))
            set_active_locale(UiLocale.EN)
            editor.set_locale()
            future: Future[object] = Future()
            future.set_exception(PaneUserStageError(
                "secret SDK path", pool=cast(object, object()),
                reason=PaneUserRefusal.CLEANUP_REQUIRED,
                failed_reason=PaneUserRefusal.PAIRED_PROFILE_CONFLICT))
            editor._future = future
            editor._operation = "prepare"
            editor._poll()
            self.assertIn("cleanup did not confirm", editor.error.text())
            self.assertIn("Earlier refusal", editor.error.text())
            self.assertIn("profiles conflict", editor.error.text())
            self.assertNotIn("secret SDK path", editor.error.text())
            editor._retained_pool = None  # Synthetic fixture owns no graph to release.
        finally:
            editor.release_after_shutdown()
            editor.close()


if __name__ == "__main__":
    unittest.main()
