"""Manual RTL tuner gain stays selected-session, discrete and UI-only."""

from __future__ import annotations

import os
from typing import cast
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import (
    AdapterRuntimeAvailability, AdapterRuntimeSnapshot, DeviceCapabilityBinding,
    DeviceFamily, RtlSessionRouteAssurance,
)
from sdr_monitor.domain.pane_scheduler import RtlRtbwPaneProfile
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError, compile_user_pane_plan


def _choice(*, selected: bool, gains: tuple[int, ...] = (-42, 0, 127)) -> AnalyzerSourceChoice:
    adapter = "rtl.librtlsdr.rx.v1"
    route = (RtlSessionRouteAssurance("RTL", "Test", "serial1", 5, 12, True,
                                      "a" * 64, 1, gains) if selected else None)
    binding = DeviceCapabilityBinding("rtl-source", DeviceFamily.RTL_SDR, adapter,
                                      rtl_session_route=route)
    runtime = AdapterRuntimeSnapshot(adapter, DeviceFamily.RTL_SDR,
                                     AdapterRuntimeAvailability.AVAILABLE, "rtl-runtime")
    return AnalyzerSourceChoice(binding, runtime, "RTL fixture", "USB")


class RtlManualGainUiTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))

    def setUp(self) -> None:
        self.locale = current_locale()
        set_active_locale(UiLocale.EN)
        self.installed: list[object] = []

    def tearDown(self) -> None:
        set_active_locale(self.locale)

    def _editor(self, choice: AnalyzerSourceChoice) -> IndependentPaneSetupV2:
        editor = IndependentPaneSetupV2(
            install=self.installed.append, uninstall=lambda: None,
            rtl_candidate_stage_available=lambda source, revision: (
                source == choice.device_id and revision == 12))
        editor.update_sources(AnalyzerSourceSelection(revision=12, choices=(choice,)))
        return editor

    def test_auto_and_exact_manual_entries_round_trip_in_tenths(self) -> None:
        choice = _choice(selected=True)
        editor = self._editor(choice)
        try:
            row = editor._rows[3]
            row.source.setCurrentIndex(row.source.findData(choice.device_id))
            self.assertTrue(row.manual_gain.isEnabled())
            self.assertEqual(tuple(row.manual_gain.itemData(i) for i in range(row.manual_gain.count())),
                             (None, -42, 0, 127))
            row.start.setValue(100)
            row.stop.setValue(101)
            for value in (None, -42, 0, 127):
                row.manual_gain.setCurrentIndex(row.manual_gain.findData(value))
                drafts = editor._read_drafts()
                draft = drafts[3]
                self.assertEqual(draft.manual_tuner_gain_tenth_db, value)
                plan = compile_user_pane_plan(drafts, {choice.device_id: choice},
                                              {choice.device_id: 12})
                assert plan.layout.schedule is not None
                profile = plan.layout.schedule.resources[0].jobs[0].profile
                self.assertIsInstance(profile, RtlRtbwPaneProfile)
                assert isinstance(profile, RtlRtbwPaneProfile)
                self.assertEqual(profile.request_template.manual_tuner_gain_tenth_db, value)
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_candidate_and_refreshed_selection_reset_manual_to_auto(self) -> None:
        candidate = _choice(selected=False)
        editor = self._editor(candidate)
        try:
            row = editor._rows[3]
            row.source.setCurrentIndex(row.source.findData(candidate.device_id))
            self.assertTrue(row.manual_gain.isEnabled())
            self.assertEqual(row.manual_gain.count(), 1)
            self.assertIsNone(editor._read_drafts()[3].manual_tuner_gain_tenth_db)
            selected = _choice(selected=True)
            editor._rtl_candidate_stage_available = lambda source, revision: True
            # A fresh revision/session makes a previous manual choice obsolete.
            editor.update_sources(AnalyzerSourceSelection(revision=13, choices=(selected,)))
            self.assertIsNone(row.manual_gain.currentData())
            self.assertEqual(tuple(row.manual_gain.itemData(i) for i in range(row.manual_gain.count())),
                             (None, -42, 0, 127))
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_invalid_bool_empty_and_unlisted_requests_refuse(self) -> None:
        for value in (True, 1001, -1001, 1.2):
            with self.subTest(value=value), self.assertRaises(PaneUserPlanError):
                PaneSlotDraft(1, "rtl-source", 100e6, 101e6,
                              manual_tuner_gain_tenth_db=value)  # type: ignore[arg-type]
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, manual_tuner_gain_tenth_db=0)
        choice = _choice(selected=True, gains=(-42, 0, 127))
        for value in (-41, 1000):
            draft = PaneSlotDraft(1, choice.device_id, 100e6, 101e6,
                                  manual_tuner_gain_tenth_db=value)
            with self.subTest(value=value), self.assertRaises(PaneUserPlanError):
                compile_user_pane_plan((draft,), {choice.device_id: choice},
                                       {choice.device_id: 12})
        unavailable = _choice(selected=True, gains=())
        draft = PaneSlotDraft(1, unavailable.device_id, 100e6, 101e6,
                              manual_tuner_gain_tenth_db=0)
        with self.assertRaises(PaneUserPlanError):
            compile_user_pane_plan((draft,), {unavailable.device_id: unavailable},
                                   {unavailable.device_id: 12})
        other = AnalyzerSourceChoice(
            DeviceCapabilityBinding("ad-source", DeviceFamily.AD936X, "ad.fixture"),
            None, "AD fixture", "USB")
        wrong_family = PaneSlotDraft(1, other.device_id, 100e6, 101e6,
                                     manual_tuner_gain_tenth_db=0)
        with self.assertRaises(PaneUserPlanError):
            compile_user_pane_plan((wrong_family,), {other.device_id: other}, {other.device_id: 1})

    def test_different_gain_requests_remain_distinct_capture_profiles(self) -> None:
        choice = _choice(selected=True)
        drafts = (
            PaneSlotDraft(1, choice.device_id, 100e6, 100.3e6,
                          sample_rate_hz=2_400_000.0,
                          manual_tuner_gain_tenth_db=-42),
            PaneSlotDraft(2, choice.device_id, 100.5e6, 100.8e6,
                          sample_rate_hz=2_400_000.0,
                          manual_tuner_gain_tenth_db=127),
        )
        plan = compile_user_pane_plan(drafts, {choice.device_id: choice}, {choice.device_id: 12})
        assert plan.layout.schedule is not None
        jobs = plan.layout.schedule.resources[0].jobs
        self.assertEqual(len(jobs), 2)
        self.assertEqual({cast(RtlRtbwPaneProfile, job.profile).request_template.manual_tuner_gain_tenth_db
                          for job in jobs},
                         {-42, 127})

    def test_en_ru_labels_and_editing_never_claims_owner_or_start(self) -> None:
        choice = _choice(selected=True)
        editor = self._editor(choice)
        try:
            row = editor._rows[3]
            row.source.setCurrentIndex(row.source.findData(choice.device_id))
            row.manual_gain.setCurrentIndex(row.manual_gain.findData(-42))
            self.assertIn("-4.2 dB", row.manual_gain.currentText())
            self.assertIn("not RF readback", row.manual_gain.accessibleDescription())
            set_active_locale(UiLocale.RU)
            editor.set_locale()
            self.assertIn("-4.2 дБ", row.manual_gain.currentText())
            self.assertIn("не RF-считывание", row.manual_gain.accessibleDescription())
            self.assertFalse(self.installed)
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session") as stage:
                stage.assert_not_called()
        finally:
            editor.release_after_shutdown()
            editor.close()


if __name__ == "__main__":
    unittest.main()
