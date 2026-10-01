"""Empty-serial route RF control is not stable/calibration identity admission."""

from __future__ import annotations

from copy import copy
from dataclasses import fields, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.application.analyzer_rf_change import AnalyzerRfChangeRejected, source_inventory
from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.domain.device_capabilities import CapabilityRange
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveSessionState
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.recording import RecordingState
from sdr_monitor.services.ad936x_capability_adapter import Ad936xCapabilityObservationError, Ad936xLibiioCapabilityAdapter
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pluto_readonly_observation import PlutoReadOnlyObserver
from sdr_monitor.services.source_capability_admission import admit_ad936x_route_request
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.workspaces.analyzer_rf_controls import default_rf_preview_text
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph

from tests.test_app06_pluto_observation_catalog import _Native
from tests.test_app07_ad936x_rtbw_pane_owner import _ReadbackEngine
from tests.ui_v2.test_app07_three_concrete_owners import _ObservedReadbackNative
from tests.ui_v2.test_app07_ad936x_sweep_pane_owner import FakeAdSweep
import tests.ui_v2.test_app07_default_rf_profile as profiles
import tests.ui_v2.test_app07_default_rf_ui as ui


def route_graph():
    native = _ObservedReadbackNative(uri="usb:empty-serial-fixture", serial="")
    def engine(uri, timeout_ms, *, expected_serial=None):
        if expected_serial is not None:
            raise AssertionError("empty serial cannot become a known expected identity")
        value = _ReadbackEngine(uri, timeout_ms)
        native.engines.append(value)
        return value
    native.PlutoFixedBandEngine = engine
    live = NativeLiveSessionService(native)
    display = FakeAdSweep(live)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),),
                                      control_transaction=live.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(live_sdr=live, analyzer_display=display,
                                                               device_catalog=catalog))
    return native, display, graph


class RouteRfCapabilitiesTests(unittest.TestCase):
    def observed(self):
        native = _Native(serial="")
        observation = PlutoReadOnlyObserver(native).observe("usb:fixture")
        return native, observation

    def selected(self):
        native, display, graph = route_graph()
        self.addCleanup(graph.live.shutdown)
        source = graph.live.discover(startup=True)[0]
        graph.live.select_device(source.device_id)
        graph.live.apply_configuration(profiles.DefaultRfProfileTests.rich_ad(None))
        return native, display, graph

    def test_empty_coherent_serial_keeps_canonical_mapping_refused_and_copies_only_observed_bounds(self):
        native, observation = self.observed()
        with self.assertRaises(Ad936xCapabilityObservationError):
            Ad936xLibiioCapabilityAdapter.map_observation(observation, "usb:fixture")
        facts = Ad936xLibiioCapabilityAdapter.map_route_observation(observation, "usb:fixture")
        self.assertEqual(facts.sample_rate_ranges_hz[0].maximum, 61.44e6)
        self.assertEqual(facts.analog_bandwidth_ranges_hz[0].maximum, 56e6)
        self.assertEqual(native.created[0].calls, ["probe", "capabilities", "topology", "disconnect"])
        self.assertFalse(hasattr(facts, "calibration_identity"))

    def test_incoherent_old_missing_malformed_known_serial_and_unknown_firmware_cannot_gain_route_bounds(self):
        _, observation = self.observed()
        for changed in (replace(observation, coherent_context=False),
                        replace(observation, probe=SimpleNamespace(**{**vars(observation.probe), "uri":"usb:other"})),
                        *[replace(observation, probe=SimpleNamespace(**{**vars(observation.probe), "serial":v}))
                          for v in (None, "unknown", "known-serial", "malformed identity")],
                        replace(observation, probe=SimpleNamespace(**{**vars(observation.probe), "firmware":"unknown"})),
                        replace(observation, capabilities=SimpleNamespace(**{**vars(observation.capabilities), "supports_continuous_iq":False})),
                        replace(observation, capabilities=SimpleNamespace(**{**vars(observation.capabilities), "sample_rate_ranges_hz":()}))):
            with self.subTest(changed=changed), self.assertRaises(Ad936xCapabilityObservationError):
                Ad936xLibiioCapabilityAdapter.map_route_observation(changed, "usb:fixture")

    def test_domain_route_units_ranges_and_stable_descriptor_join_fail_closed(self):
        native, _, graph = self.selected()
        device = graph.live.current_snapshot().device
        facts = device.route_rf_capabilities
        for changed in ({"uri":"ip:"}, {"firmware":"-"}, {"sample_rate_ranges_hz":()},
                        {"tuning_ranges_hz":(CapabilityRange(70e6, 6e9, "dB"),)},
                        {"analog_bandwidth_ranges_hz":(CapabilityRange(0, 56e6, "Hz"),)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(facts, **changed)
        with self.assertRaises(ValueError):
            replace(device, serial="invented")
        self.assertEqual(native.engines, [])

    def test_unknown_routes_do_not_merge_publish_stable_capabilities_or_calibration_keys(self):
        native = _Native(serial="")
        service = NativeLiveSessionService(native)
        descriptors = service.discover_devices()
        self.assertEqual(len(descriptors), 2)
        self.assertNotEqual(descriptors[0].device_id, descriptors[1].device_id)
        self.assertTrue(all(v.route_rf_capabilities is not None and v.serial is None
                            and v.capability_snapshot is None and v.calibration_identity is None for v in descriptors))
        inventory = service.capability_inventory()
        self.assertEqual(inventory.snapshots, ())
        self.assertTrue(all(v.identity_key is None and v.calibration_identity is None for v in inventory.bindings))

    def test_high_fs_preview_reuses_selected_owned_bounds_without_io(self):
        native, _, graph = self.selected()
        before = graph.live.current_snapshot()
        count = len(native.created)
        proposal = graph.live.preview_rf_shift(5e6)
        self.assertIs(proposal.expected.route_rf_capabilities, before.device.route_rf_capabilities)
        self.assertEqual(proposal.request.center_hz, before.applied.applied.center_hz + 5e6)
        self.assertIs(graph.live.current_snapshot(), before)
        self.assertEqual(len(native.created), count)
        self.assertEqual(native.engines, [])
        with self.assertRaises(AnalyzerRfChangeRejected):
            source_inventory(proposal.expected)

    def test_bounds_refuse_tuning_fs_filter_and_gain_without_lowering_or_clamping(self):
        native, _, graph = self.selected()
        context = graph.live.capture_rf_context()
        facts = context.route_rf_capabilities
        for change in ({"center_hz":6.1e9}, {"sample_rate_hz":80e6},
                       {"analog_bandwidth_hz":60e6}, {"gain_db":100}):
            self.assertFalse(admit_ad936x_route_request(facts, replace(context.request, **change)).accepted)
        with self.assertRaises(LiveAdmissionRejected):
            graph.live.preview_rf_shift(6e9)
        self.assertEqual(native.engines, [])

    def test_full_rtbw_stop_apply_ack_start_preserves_non_frequency_fields_and_actual_epochs(self):
        native, _, graph = self.selected()
        graph.live.start()
        before = graph.live.capture_rf_context()
        proposal = graph.live.preview_rf_shift(5e6)
        graph.live.stop_rf_rtbw(proposal, cancelled=lambda:False)
        receipt = graph.live.apply_rf_shift(proposal, cancelled=lambda:False)
        self.assertEqual(len(native.engines), 1)
        with self.assertRaises(AnalyzerRfChangeRejected):
            graph.live.start_rf_rtbw(receipt, cancelled=lambda:False)
        graph.live.acknowledge_rf_apply(receipt)
        after = graph.live.start_rf_rtbw(receipt, cancelled=lambda:False)
        self.assertIs(after.state, LiveSessionState.RUNNING)
        self.assertGreater(after.acquisition_epoch, before.acquisition_epoch)
        for field in fields(before.request):
            if field.name != "center_hz":
                self.assertEqual(getattr(before.request, field.name), getattr(after.applied.applied, field.name))
        self.assertIs(after.device.route_rf_capabilities, before.route_rf_capabilities)
        self.assertIsNone(after.device.calibration_identity)

    def test_stopped_apply_arms_only_and_retains_exact_route_reference(self):
        native, _, graph = self.selected()
        proposal = graph.live.preview_rf_shift(5e6)
        receipt = graph.live.apply_rf_shift(proposal, cancelled=lambda:False)
        graph.live.acknowledge_rf_apply(receipt)
        self.assertEqual(native.engines, [])
        self.assertIs(graph.analyzer.state.phase, AnalyzerPhase.IDLE)
        self.assertIs(receipt.armed_context.route_rf_capabilities, proposal.expected.route_rf_capabilities)
        graph.live.start_rf_rtbw(receipt, cancelled=lambda:False)
        self.assertEqual(len(native.engines), 1)

    def test_equal_copied_facts_and_changed_selected_uri_are_not_authority_before_stop(self):
        native, _, graph = self.selected()
        graph.live.start()
        context = graph.live.capture_rf_context()
        service = graph.services.live_sdr
        for facts in (copy(context.route_rf_capabilities), replace(context.route_rf_capabilities)):
            with self.assertRaises(LiveAdmissionRejected):
                service.preflight_rf_route(context.source.device_id, facts, context.request)
        proposal = graph.live.preview_rf_shift(5e6)
        with patch.object(service, "_native_uri", "usb:other"), patch.object(service, "stop") as stop:
            with self.assertRaises(LiveAdmissionRejected):
                graph.live.stop_rf_rtbw(proposal, cancelled=lambda:False)
            stop.assert_not_called()
        self.assertIs(graph.analyzer.state.phase, AnalyzerPhase.RUNNING)
        self.assertEqual(len(native.engines), 1)

    def test_unavailable_native_contract_and_pending_release_refuse_route_before_effects(self):
        native, _, graph = self.selected()
        service = graph.services.live_sdr
        for name, value in (("PLUTO_OBSERVATION_PROTOCOL_VERSION", 0), ("PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION", 0)):
            with patch.object(native, name, value), self.assertRaises(LiveAdmissionRejected):
                graph.live.preview_rf_shift(5e6)
            with patch.object(native, name, value):
                self.assertIsNotNone(service._start_capability_refusal())
        with patch.object(service, "_stream_release_failed", True), self.assertRaises(LiveAdmissionRejected):
            graph.live.preview_rf_shift(5e6)
        self.assertEqual(native.engines, [])

    def test_reselection_same_values_has_new_route_facts_and_refuses_old_proposal(self):
        native, _, graph = self.selected()
        proposal = graph.live.preview_rf_shift(5e6)
        graph.live.select_device(proposal.expected.source.device_id)
        graph.live.apply_configuration(proposal.expected.request)
        current = graph.live.capture_rf_context()
        self.assertIsNot(current.route_rf_capabilities, proposal.expected.route_rf_capabilities)
        self.assertFalse(proposal.expected.matches(current))
        with self.assertRaises(AnalyzerRfChangeRejected):
            graph.live.apply_rf_shift(proposal, cancelled=lambda:False)
        self.assertEqual(native.engines, [])

    def test_recording_guard_is_not_bypassed_by_empty_serial_route(self):
        native, _, graph = self.selected()
        graph.live.start()
        proposal = graph.live.preview_rf_shift(5e6)
        with patch.object(graph.services.live_sdr, "native_recording_health", return_value=SimpleNamespace(state=RecordingState.RECORDING)), \
             patch.object(graph.services.live_sdr, "stop") as stop:
            with self.assertRaises(AnalyzerRfChangeRejected):
                graph.live.stop_rf_rtbw(proposal, cancelled=lambda:False)
            stop.assert_not_called()
        self.assertEqual(len(native.engines), 1)

    def test_sweep_uses_same_observed_bounds_and_full_live_profile(self):
        native, _, graph = self.selected()
        context = graph.live.capture_rf_context()
        request = ContinuousSweepPlanRequest(100e6, 220e6, epoch=1, analysis_bins_per_usable_window=4096)
        service = graph.services.live_sdr
        service.preflight_rf_route(context.source.device_id, context.route_rf_capabilities,
                                   request, applied_live=context.request)
        for bad in (replace(request, stop_hz=6.1e9), replace(request, usable_window_hz=57e6)):
            with self.assertRaises(LiveAdmissionRejected):
                service.preflight_rf_route(context.source.device_id, context.route_rf_capabilities,
                                           bad, applied_live=context.request)
        self.assertEqual(native.engines, [])

    def test_route_preview_warning_is_explicit_in_both_locales_without_route_or_serial_dump(self):
        _, _, graph = self.selected()
        proposal = graph.live.preview_rf_shift(5e6)
        previous = current_locale()
        try:
            for locale in (UiLocale.RU, UiLocale.EN):
                set_active_locale(locale)
                detail = default_rf_preview_text(proposal, (1, 2, 3, 4))
                self.assertIn(text("analyzer.rf.route_scope"), detail)
                self.assertNotIn(proposal.expected.route_rf_capabilities.uri, detail)
        finally:
            set_active_locale(previous)


class RouteRfUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ui.QApplication.instance() or ui.QApplication([])
    wait = ui.DefaultRfUiTests.wait
    product = ui.DefaultRfUiTests.product
    publish_ad = ui.DefaultRfUiTests.publish_ad
    start_rtbw = ui.DefaultRfUiTests.start_rtbw
    preview = ui.DefaultRfUiTests.preview

    def test_actual_four_view_default_cancel_and_approved_receipt_before_start_on_empty_serial(self):
        with patch.object(ui, "ad_sweep_graph", side_effect=route_graph), self.product() as p:
            self.start_rtbw(p, views=4)
            before = p.application.capture_rf_context()
            dialog = self.preview(p)
            self.assertIn(text("analyzer.rf.route_scope"), dialog.details.toPlainText())
            self.assertTrue(dialog.cancel_button.isDefault())
            dialog.reject()
            self.wait(lambda: not p.controller.pending)
            self.assertTrue(before.matches(p.application.capture_rf_context()))
            observed = []
            original = p.application.start_rf_rtbw
            def start(receipt, **kw):
                observed.append(all(v.last_bundle is None and v._last_waterfall is None and v._last_persistence is None
                                    and v.spectrum_scene.latest_frame is None for v in p.page._panes))
                return original(receipt, **kw)
            with patch.object(p.application, "start_rf_rtbw", side_effect=start):
                self.preview(p).confirm_button.click()
                self.wait(lambda: not p.controller.pending and p.page.model.state.running)
            self.assertEqual(observed, [True])
            after = p.application.capture_rf_context()
            self.assertIs(after.route_rf_capabilities, before.route_rf_capabilities)
            self.assertGreater(after.acquisition_epoch, before.acquisition_epoch)
            self.assertEqual(after.request.center_hz, before.request.center_hz + 5e6)
            self.publish_ad(p, 3)


if __name__ == "__main__":
    unittest.main()
