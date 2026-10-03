"""RTL pane editor stays inert until the exact product owner admits RTBW."""

from __future__ import annotations

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
    AdapterRuntimeAvailability, AdapterRuntimeSnapshot, DeviceCapabilityBinding,
    DeviceFamily, RtlSessionRouteAssurance,
)
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, CaptureMeasurementMode, PaneLayoutSlot, RtlRtbwPaneProfile,
)
from sdr_monitor.domain.receiver_topology import SweepPaneRequest
from sdr_monitor.domain.rtl_live import RTL_FFT_CHOICES, RTL_RATE_CHOICES_HZ, RtlLiveRequest
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_user_stage import PreparedPaneUserSession
from sdr_monitor.ui.v2_pane_user_plan import RtbwBandPolicy


def _choice(*, runtime: AdapterRuntimeAvailability, selected_route: bool = False) -> AnalyzerSourceChoice:
    family = DeviceFamily.RTL_SDR
    adapter = "rtl.librtlsdr.rx.v1"
    route = (RtlSessionRouteAssurance("RTL", "SDR", "00000001", 5, 1, True, "a" * 64)
             if selected_route else None)
    binding = DeviceCapabilityBinding("rtl-source", family, adapter, rtl_session_route=route)
    observation = AdapterRuntimeSnapshot(adapter, family, runtime, "rtl-runtime")
    return AnalyzerSourceChoice(binding, observation, "RTL-SDR fixture", "USB")


class RtlPaneSetupTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))

    def setUp(self) -> None:
        self.original_locale = current_locale()
        set_active_locale(UiLocale.EN)
        self.installed: list[object] = []

    def tearDown(self) -> None:
        set_active_locale(self.original_locale)

    def _editor(self, choice: AnalyzerSourceChoice, *, owner_ready=False) -> IndependentPaneSetupV2:
        editor = IndependentPaneSetupV2(
            install=self.installed.append, uninstall=lambda: None,
            rtl_candidate_stage_available=lambda source, revision: owner_ready
            and source == choice.device_id and revision == 17,
        )
        editor.update_sources(AnalyzerSourceSelection(revision=17, choices=(choice,)))
        return editor

    @staticmethod
    def _item_enabled(combo: QComboBox, index: int) -> bool:
        model = combo.model()
        assert isinstance(model, QStandardItemModel)
        item = model.item(index)
        assert item is not None
        return item.isEnabled()

    def test_unavailable_observed_choice_is_visible_but_never_stage_ready(self) -> None:
        choice = _choice(runtime=AdapterRuntimeAvailability.UNAVAILABLE)
        editor = self._editor(choice, owner_ready=True)
        try:
            row = editor._rows[3]
            index = row.source.findData(choice.device_id)
            self.assertGreater(index, 0)
            self.assertFalse(self._item_enabled(row.source, index))
            self.assertIn("unavailable", row.source.itemText(index))
            row.source.setCurrentIndex(index)  # A retained draft can still refer to this item.
            self.assertEqual(tuple(row.rate.itemData(i) for i in range(row.rate.count())),
                             tuple(float(value) for value in sorted(RTL_RATE_CHOICES_HZ)))
            self.assertEqual(tuple(row.fft.itemData(i) for i in range(row.fft.count())),
                             tuple(sorted(RTL_FFT_CHOICES)))
            self.assertEqual(row.mode.count(), 1)
            self.assertEqual(CaptureMeasurementMode(row.mode.currentData()), CaptureMeasurementMode.RTBW)
            self.assertFalse(row.rate.isEnabled())
            self.assertFalse(row.band.isEnabled())
            self.assertFalse(editor.prepare.isEnabled())
            self.assertIn("runtime", row.source.toolTip())
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session") as stage:
                editor._begin_prepare()
                stage.assert_not_called()
            self.assertEqual(editor._error_key, "analyzer.pane.setup.rtl_runtime_unavailable")
            set_active_locale(UiLocale.RU)
            editor.set_locale()
            self.assertIn("недоступен", row.source.itemText(index))
            self.assertIn("RTL-SDR", row.source.accessibleDescription())
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_runtime_or_route_alone_do_not_admit_candidate(self) -> None:
        cases = (
            (_choice(runtime=AdapterRuntimeAvailability.AVAILABLE), False,
             "analyzer.pane.setup.rtl_owner_unavailable"),
            (_choice(runtime=AdapterRuntimeAvailability.AVAILABLE, selected_route=True), False,
             "analyzer.pane.setup.rtl_owner_unavailable"),
        )
        for choice, owner_ready, reason in cases:
            with self.subTest(reason=reason):
                editor = self._editor(choice, owner_ready=owner_ready)
                try:
                    row = editor._rows[3]
                    index = row.source.findData(choice.device_id)
                    self.assertFalse(self._item_enabled(row.source, index))
                    row.source.setCurrentIndex(index)
                    self.assertFalse(editor.prepare.isEnabled())
                    self.assertEqual(editor._rtl_unavailable_key(choice), reason)
                finally:
                    editor.release_after_shutdown()
                    editor.close()

    def test_wrong_adapter_refuses_even_when_callback_claims_stage_ready(self) -> None:
        binding = DeviceCapabilityBinding("rtl-source", DeviceFamily.RTL_SDR, "rtl.other.rx")
        runtime = AdapterRuntimeSnapshot("rtl.other.rx", DeviceFamily.RTL_SDR,
                                         AdapterRuntimeAvailability.AVAILABLE, "rtl-runtime")
        choice = AnalyzerSourceChoice(binding, runtime, "Wrong RTL adapter", "USB")
        editor = self._editor(choice, owner_ready=True)
        try:
            row = editor._rows[3]
            index = row.source.findData(choice.device_id)
            self.assertFalse(self._item_enabled(row.source, index))
            row.source.setCurrentIndex(index)
            self.assertFalse(editor.prepare.isEnabled())
            self.assertEqual(editor._rtl_unavailable_key(choice),
                             "analyzer.pane.setup.rtl_capability_unverified")
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_exact_owner_callback_only_enables_rtbw_draft_without_starting_receiver(self) -> None:
        choice = _choice(runtime=AdapterRuntimeAvailability.AVAILABLE)
        editor = self._editor(choice, owner_ready=False)
        try:
            row = editor._rows[3]
            index = row.source.findData(choice.device_id)
            self.assertFalse(self._item_enabled(row.source, index))
            editor._rtl_candidate_stage_available = lambda source, revision: (
                source == choice.device_id and revision == 17)
            editor.update_sources(editor._selection)
            index = row.source.findData(choice.device_id)
            self.assertTrue(self._item_enabled(row.source, index))
            row.source.setCurrentIndex(index)
            self.assertTrue(editor.prepare.isEnabled())
            self.assertIn("inventory-only", row.source.toolTip())
            self.assertIn("not yet a selected tuner", row.source.accessibleDescription())
            self.assertEqual(tuple(row.mode.itemData(i) for i in range(row.mode.count())),
                             (CaptureMeasurementMode.RTBW,))
            self.assertTrue(row.rate.isEnabled())
            self.assertTrue(row.fft.isEnabled())
            self.assertFalse(row.band.isEnabled())
            row.rate.setCurrentIndex(row.rate.findData(2_400_000.0))
            row.fft.setCurrentIndex(row.fft.findData(2048))
            draft = editor._read_drafts()[3]
            self.assertEqual((draft.sample_rate_hz, draft.fft_size), (2_400_000.0, 2048))
            self.assertIs(draft.measurement_mode, CaptureMeasurementMode.RTBW)
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_cached_rtl_stage_candidate_does_not_require_default_pluto_selection(self) -> None:
        rtl = _choice(runtime=AdapterRuntimeAvailability.AVAILABLE)
        ad = AnalyzerSourceChoice(DeviceCapabilityBinding(
            "ad-source", DeviceFamily.AD936X, "ad.fixture"), None, "Pluto fixture", "USB")
        editor = IndependentPaneSetupV2(
            install=self.installed.append, uninstall=lambda: None,
            rtl_candidate_stage_available=lambda source, revision: source == rtl.device_id and revision == 17)
        try:
            editor.update_sources(AnalyzerSourceSelection(
                revision=17, choices=(ad, rtl), selected_id=ad.device_id))
            row = editor._rows[3]
            index = row.source.findData(rtl.device_id)
            self.assertTrue(self._item_enabled(row.source, index))
            row.source.setCurrentIndex(index)
            self.assertTrue(editor.prepare.isEnabled())
            self.assertIn("inventory-only", row.source.toolTip())
            self.assertIsNone(rtl.binding.identity_key)
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_selected_session_route_is_not_stable_calibration_identity(self) -> None:
        rtl = _choice(runtime=AdapterRuntimeAvailability.AVAILABLE, selected_route=True)
        editor = self._editor(rtl, owner_ready=True)
        try:
            row = editor._rows[3]
            row.source.setCurrentIndex(row.source.findData(rtl.device_id))
            self.assertTrue(editor.prepare.isEnabled())
            self.assertIn("only for this USB session", row.source.toolTip())
            self.assertIn("indistinguishable compatible replacement", row.source.toolTip())
            self.assertIsNone(rtl.binding.identity_key)
            self.assertIsNone(rtl.binding.calibration_identity)
            set_active_locale(UiLocale.RU)
            editor.set_locale()
            self.assertIn("USB-сеанса", row.source.toolTip())
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_rtl_preview_does_not_invent_filter_or_actual_readback(self) -> None:
        choice = _choice(runtime=AdapterRuntimeAvailability.AVAILABLE)
        editor = self._editor(choice, owner_ready=True)
        try:
            request = RtlLiveRequest(100_500_000, 2_400_000)
            profile = RtlRtbwPaneProfile(request, 1_500_000.0,
                                         CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005))
            crop = SimpleNamespace(pane_id="pane-4", start_hz=100_000_000, stop_hz=101_000_000)
            job = SimpleNamespace(profile=profile, crops=(crop,))
            resource_id = "pane-resource-4"
            schedule = SimpleNamespace(resources=(SimpleNamespace(
                physical_stream_resource_id=resource_id, jobs=(job,)),))
            slots = tuple(PaneLayoutSlot(number) for number in range(1, 4)) + (
                PaneLayoutSlot(4, SweepPaneRequest(
                    "pane-4", "rtl-endpoint-4", 100_000_000, 101_000_000)),)
            plan = SimpleNamespace(resource_sources=((resource_id, choice.device_id),),
                                   groups=(), scheduler_intents=(),
                                   ad_sweep_geometry=(), paired_sweep_geometry=(),
                                   paired_sweep_requested_crops=(), hackrf_sweep_geometry=(),
                                   hackrf_hardware_ranges=(),
                                   layout=SimpleNamespace(slots=slots, schedule=schedule))
            resource_preview = SimpleNamespace(
                physical_stream_resource_id=resource_id, affected_pane_ids=("pane-4",),
                capture_job_count=1, recording_conflict=False, revisit_estimates=())
            editor._prepared = cast(PreparedPaneUserSession, SimpleNamespace(
                plan=plan, preview=(resource_preview,),
                handle=SimpleNamespace(source_labels={choice.device_id: choice.label}, applied=False)))
            editor._refresh_preview()
            preview = editor.preview.text()
            self.assertIn("panes 4", editor.impact_summary.text())
            self.assertIn("Empty 1, 2, 3", editor.impact_summary.text())
            self.assertIn("requested Fs 2.4 MS/s", preview)
            self.assertIn("planned digital analysis crop", preview)
            self.assertIn("not a guaranteed RF passband", preview)
            self.assertIn("uncalibrated dBFS/bin", preview)
            self.assertIn("Actual Fs/center are unknown", preview)
            self.assertIn("gain mode is auto, actual gain unknown", preview)
            self.assertNotIn("RF filter", preview)
            self.assertNotIn("HackRF acknowledges", preview)
            self.assertIsNone(editor._future)
            self.assertFalse(self.installed)
        finally:
            editor._prepared = None
            editor.release_after_shutdown()
            editor.close()

    def test_switching_from_ad_full_receive_resets_only_new_rtl_draft_band(self) -> None:
        rtl = _choice(runtime=AdapterRuntimeAvailability.AVAILABLE)
        ad = AnalyzerSourceChoice(DeviceCapabilityBinding(
            "ad-source", DeviceFamily.AD936X, "ad.fixture"), None, "AD fixture", "USB")
        editor = IndependentPaneSetupV2(
            install=self.installed.append, uninstall=lambda: None,
            rtl_candidate_stage_available=lambda source, revision: source == rtl.device_id and revision == 17)
        try:
            editor.update_sources(AnalyzerSourceSelection(revision=17, choices=(ad, rtl)))
            peer, target = editor._rows[0], editor._rows[3]
            for row in (peer, target):
                row.source.setCurrentIndex(row.source.findData(ad.device_id))
                row.band.setCurrentIndex(row.band.findData(RtbwBandPolicy.FULL_RECEIVE.value))
                self.assertIs(RtbwBandPolicy(row.band.currentData()), RtbwBandPolicy.FULL_RECEIVE)
            target.source.setCurrentIndex(target.source.findData(rtl.device_id))
            self.assertIs(RtbwBandPolicy(target.band.currentData()), RtbwBandPolicy.EDGE_TRIMMED)
            self.assertFalse(target.band.isEnabled())
            self.assertIs(RtbwBandPolicy(peer.band.currentData()), RtbwBandPolicy.FULL_RECEIVE)
            drafts = editor._read_drafts()
            self.assertIs(drafts[3].rtbw_band, RtbwBandPolicy.EDGE_TRIMMED)
            self.assertIs(drafts[0].rtbw_band, RtbwBandPolicy.FULL_RECEIVE)
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()

    def test_owner_callback_exception_and_stale_selection_keep_stage_inert(self) -> None:
        choice = _choice(runtime=AdapterRuntimeAvailability.AVAILABLE)
        editor = self._editor(choice, owner_ready=False)
        try:
            row = editor._rows[3]
            row.source.setCurrentIndex(row.source.findData(choice.device_id))

            def broken_owner(_source: str, _revision: int) -> bool:
                raise RuntimeError("internal owner detail must not reach UI")

            editor._rtl_candidate_stage_available = broken_owner
            editor.update_sources(editor._selection)
            self.assertFalse(editor.prepare.isEnabled())
            self.assertEqual(editor._rtl_unavailable_key(choice),
                             "analyzer.pane.setup.rtl_owner_unavailable")
            editor.update_sources(AnalyzerSourceSelection(
                revision=18, choices=(choice,), release_pending=True))
            self.assertEqual(editor._rtl_unavailable_key(choice),
                             "analyzer.pane.setup.rtl_selection_unavailable")
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session") as stage:
                editor._begin_prepare()
                stage.assert_not_called()
            self.assertFalse(self.installed)
        finally:
            editor.release_after_shutdown()
            editor.close()


if __name__ == "__main__":
    unittest.main()
