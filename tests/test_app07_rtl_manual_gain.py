"""Typed RTL gain admission/ownership tests. Mock only, never a hardware proof."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import RtlSessionRouteAssurance
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveSessionState
from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from sdr_monitor.services.rtl_capability_provider import (
    RTL_ADAPTER_ID, RTL_SOURCE_ID, RtlCapabilityProvider, RtlRuntimeProvision,
    qualified_rtl_runtime,
)
from sdr_monitor.services.source_capability_admission import admit_source_request
from scripts.preflight_sdr_native_build import ContractSurfaceError, validate_rtl_factory
from tests.test_app07_rtl_product_route import _Control, _Exclusion, _Native


class GainNative(_Native):
    RTLSDR_TUNER_GAIN_CONTRACT_VERSION = 1

    def __init__(self, gains=(-99, 0, 144, 496), tuner_type=5):
        super().__init__()
        self.gains, self.tuner_type = gains, tuner_type
        self.kwargs = []
        self.cached_override = "correct"
        self.known = True

    def rtl_observe_single_candidate(self, _runtime):
        return SimpleNamespace(enumeration_index=0, manufacturer="Generic", product="RTL tuner",
            serial="00000001", tuner_type=self.tuner_type, direct_sampling=False,
            offset_tuning=False, tuner_gains_tenth_db=self.gains)

    def create_rtl_runtime_control(self, _runtime, center, rate, *_args, **kwargs):
        self.create_calls += 1
        self.kwargs.append(kwargs)
        control = _Control(center, rate)
        gain = kwargs.get("manual_tuner_gain_tenth_db")
        cached = gain if self.cached_override == "correct" else self.cached_override
        control.readback = lambda: SimpleNamespace(session_epoch=17, actual_center_hz=center,
            actual_sample_rate_hz=rate, tuner_gain_readback_known=self.known,
            cached_tuner_gain_tenth_db=cached)
        self.control = control
        return control


def fixture(native=None, *, qualified_gain=True):
    native = GainNative() if native is None else native
    provision = RtlRuntimeProvision(native, object(), "b" * 64, "c" * 64,
        1 if qualified_gain else None)
    provider = RtlCapabilityProvider(provision)
    provider.discover(startup_only=False)
    inventory = provider.observe_source(RTL_SOURCE_ID)
    binding = inventory.binding_for_source(RTL_SOURCE_ID)
    runtime = inventory.runtime_for_adapter(RTL_ADAPTER_ID)
    choice = AnalyzerSourceChoice(binding, runtime, "mock selected RTL", "USB SESSION")
    selection = AnalyzerSourceSelection(3, (choice,), RTL_SOURCE_ID)
    exclusion = _Exclusion()
    service = RtlAnalyzerService(native, exclusion, lambda: inventory,
        lambda binding, _runtime: provider.provision_for(binding))
    service.bind_selection(selection)
    return native, provider, inventory, selection, exclusion, service


def request(gain=None):
    return RtlLiveRequest(150_000_000, 2_400_000, source_id=RTL_SOURCE_ID,
        manual_tuner_gain_tenth_db=gain)


class RtlManualGainTests(unittest.TestCase):
    def test_exact_integer_intent_defaults_and_refusals(self):
        for gain in (None, -99, 0, 144):
            with self.subTest(gain=gain):
                value = request(gain)
                self.assertEqual(value.tuner_gain_mode, "automatic" if gain is None else "manual")
                self.assertEqual(value.manual_tuner_gain_tenth_db, gain)
        for bad in (True, False, 14.4, "144", -1001, 1001):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                request(bad)

    def test_route_is_discrete_not_a_range_or_fc2580_sentinel(self):
        base = RtlSessionRouteAssurance("Generic", "RTL tuner", "00000001", 5, 7, True, "a" * 64)
        self.assertFalse(base.manual_gain_available)
        manual = replace(base, manual_gain_contract_version=1, tuner_gains_tenth_db=(-99, 0, 144))
        self.assertTrue(manual.manual_gain_available)
        for patch in ({"tuner_gains_tenth_db": (0,)}, {"manual_gain_contract_version": True},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": [0]},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": (0, 0)},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": (144, 0)},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": (False,)},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": (1001,)},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": tuple(range(257))},
                      {"manual_gain_contract_version": 1, "tuner_gains_tenth_db": (0,), "tuner_type": 4}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                replace(base, **patch)

    def test_optional_bad_table_keeps_auto_and_refuses_manual_inertly(self):
        for values, tuner, qualified in (((), 5, True), ((0,), 4, True),
                ((0, 0), 5, True), ((144, 0), 5, True), ((True,), 5, True),
                ((1001,), 5, True), (tuple(range(257)), 5, True),
                ((0, 144), 5, False)):
            with self.subTest(values=values, tuner=tuner, qualified=qualified):
                native, _, inventory, selection, exclusion, service = fixture(
                    GainNative(values, tuner), qualified_gain=qualified)
                self.assertTrue(admit_source_request(inventory, RTL_SOURCE_ID, "rtbw", request()).accepted)
                self.assertFalse(admit_source_request(inventory, RTL_SOURCE_ID, "rtbw", request(0)).accepted)
                with self.assertRaises(LiveAdmissionRejected):
                    service.stage(RtlConfigurationPatch(request(0), selection.revision, 0))
                self.assertEqual(native.create_calls, 0)
                self.assertFalse(exclusion.claimed)
                service.stage(RtlConfigurationPatch(request(), selection.revision, 0))
                try:
                    self.assertIs(service.start().state, LiveSessionState.RUNNING)
                    self.assertEqual(native.kwargs, [{}])
                finally:
                    service.stop()

    def test_stage_start_cache_and_auto_restaging_zero_is_manual(self):
        for gain in (-99, 0, 144):
            with self.subTest(gain=gain):
                native, _, _, selection, exclusion, service = fixture()
                staged = service.stage(RtlConfigurationPatch(request(gain), selection.revision, 0))
                self.assertEqual(native.create_calls, 0)
                self.assertIsNone(staged.rtl_cached_tuner_gain_tenth_db)
                try:
                    started = service.start()
                    self.assertIs(started.state, LiveSessionState.RUNNING)
                    self.assertEqual(started.rtl_cached_tuner_gain_tenth_db, gain)
                    self.assertEqual(native.kwargs, [{"manual_tuner_gain_tenth_db": gain}])
                    self.assertTrue(exclusion.claimed)
                    with self.assertRaises(ValueError):
                        replace(started, rtl_cached_tuner_gain_tenth_db=True)
                    with self.assertRaises(ValueError):
                        replace(started, rtl_cached_tuner_gain_tenth_db=gain + 1)
                    for epoch in (None, True, 0, -1):
                        with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                            replace(started, acquisition_epoch=epoch)
                    with self.assertRaises(ValueError):
                        replace(started, rtl_cached_tuner_gain_tenth_db=None)
                finally:
                    stopped = service.stop()
                self.assertFalse(stopped.stop_required)
                self.assertFalse(exclusion.claimed)
                auto = service.stage(RtlConfigurationPatch(request(), selection.revision, stopped.generation))
                self.assertIsNone(auto.rtl_cached_tuner_gain_tenth_db)
                with self.assertRaises(ValueError):
                    replace(auto, acquisition_epoch=17, rtl_cached_tuner_gain_tenth_db=gain)
                try:
                    self.assertIs(service.start().state, LiveSessionState.RUNNING)
                    self.assertEqual(native.kwargs[-1], {})
                finally:
                    service.stop()

    def test_invalid_gain_entry_and_changed_bridge_never_acquire_owner(self):
        native, _, _, selection, exclusion, service = fixture()
        with self.assertRaises(LiveAdmissionRejected):
            service.stage(RtlConfigurationPatch(request(145), selection.revision, 0))
        service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
        native.RTLSDR_TUNER_GAIN_CONTRACT_VERSION = True
        with self.assertRaisesRegex(LiveAdmissionRejected, "bridge"):
            service.start()
        self.assertEqual(native.create_calls, 0)
        self.assertFalse(exclusion.claimed)

    def test_refresh_after_stage_invalidates_gain_session_before_factory(self):
        native, provider, _, selection, exclusion, service = fixture()
        service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
        provider.discover(startup_only=False)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            service.start()
        self.assertEqual(native.create_calls, 0)
        self.assertFalse(exclusion.claimed)

    def test_control_version_bool_is_not_an_integer_contract(self):
        native, _, _, selection, exclusion, service = fixture()
        service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
        native.RTLSDR_RX_CONTROL_CONTRACT_VERSION = True
        self.assertFalse(service.native_control_available())
        with self.assertRaisesRegex(LiveAdmissionRejected, "protocol"):
            service.start()
        self.assertFalse(exclusion.claimed)
        self.assertEqual(native.create_calls, 0)

    def test_bad_cache_keeps_exact_owner_until_explicit_stop(self):
        for cached, known in ((None, True), (True, True), (145, True), ("144", True), (144, False)):
            with self.subTest(cached=cached, known=known):
                native = GainNative()
                native.cached_override, native.known = cached, known
                _, _, _, selection, exclusion, service = fixture(native)
                service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
                try:
                    failed = service.start()
                    self.assertIs(failed.state, LiveSessionState.ERROR)
                    self.assertTrue(failed.stop_required)
                    self.assertIsNone(failed.rtl_cached_tuner_gain_tenth_db)
                    self.assertTrue(exclusion.claimed)
                    with self.assertRaises(LiveAdmissionRejected):
                        service.start()
                finally:
                    service.stop()
                self.assertFalse(exclusion.claimed)
                self.assertEqual(native.control.stops, 1)

    def test_factory_failure_releases_only_confirmed_nonquarantined_cleanup(self):
        class FailedFactory(GainNative):
            quarantined = False

            def rtl_process_is_quarantined(self):
                return self.quarantined

            def create_rtl_runtime_control(self, *_args, **_kwargs):
                self.create_calls += 1
                raise RuntimeError("mock manual setter failure; no control returned")

        for ambiguous in (False, True):
            native = FailedFactory()
            _, _, _, selection, exclusion, service = fixture(native)
            service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
            # Factory can set quarantine during a failed close, but only AFTER
            # pre-admission observed a nonquarantined process.
            factory = native.create_rtl_runtime_control

            def fail(*args, **kwargs):
                native.quarantined = ambiguous
                return factory(*args, **kwargs)

            native.create_rtl_runtime_control = fail
            failed = service.start()
            self.assertIs(failed.state, LiveSessionState.ERROR)
            self.assertTrue(failed.stop_required)
            self.assertTrue(exclusion.claimed)
            self.assertIsNone(native.control)
            stopped = service.stop()
            self.assertIs(stopped.state, LiveSessionState.ERROR if ambiguous else LiveSessionState.CONNECTED)
            self.assertEqual(stopped.stop_required, ambiguous)
            self.assertEqual(exclusion.claimed, ambiguous)
            self.assertEqual(native.create_calls, 1)
            if ambiguous:
                repeated = service.stop()
                self.assertTrue(repeated.stop_required)
                self.assertTrue(exclusion.claimed)
                with self.assertRaises(LiveAdmissionRejected):
                    service.start()

    def test_superficially_complete_stop_does_not_release_quarantined_owner(self):
        native, _, _, selection, exclusion, service = fixture()
        service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
        self.assertIs(service.start().state, LiveSessionState.RUNNING)
        native.rtl_process_is_quarantined = lambda: True
        stopped = service.stop()
        self.assertIs(stopped.state, LiveSessionState.ERROR)
        self.assertTrue(stopped.stop_required)
        self.assertTrue(exclusion.claimed)
        self.assertEqual(native.control.stops, 1)

    def test_unknown_cleanup_status_keeps_owner_and_first_cause(self):
        native, _, _, selection, exclusion, service = fixture()
        service.stage(RtlConfigurationPatch(request(144), selection.revision, 0))
        self.assertIs(service.start().state, LiveSessionState.RUNNING)

        def unconfirmed():
            raise RuntimeError("mock status observation failed")

        native.rtl_process_is_quarantined = unconfirmed
        stopped = service.stop()
        self.assertTrue(stopped.stop_required)
        self.assertTrue(exclusion.claimed)
        self.assertEqual(service.first_fault_diagnostic()[0], "native_cleanup_status")

    def test_bridge_manifest_must_match_exact_native_gain_version(self):
        names = ("RtlExternalFile", "RtlExternalRuntime", "RtlSessionRoute", "rtl_enumerate_candidates",
                 "rtl_observe_single_candidate", "create_rtl_runtime_control", "rtl_process_is_quarantined")
        module = SimpleNamespace(RTLSDR_OFFICIAL_COMPILED=True, RTLSDR_RX_CONTROL_CONTRACT_VERSION=1,
            **{name: lambda: None for name in names})
        manifest = {"rtl_official_compiled": True, "rtl_control_contract_version": 1}
        validate_rtl_factory(module, manifest)  # legacy auto-only native is valid
        module.RTLSDR_TUNER_GAIN_CONTRACT_VERSION = 1
        with self.assertRaises(ContractSurfaceError):
            validate_rtl_factory(module, manifest)
        validate_rtl_factory(module, {**manifest, "rtl_tuner_gain_contract_version": 1})
        for bad in (None, True, "1", 0, 2):
            with self.subTest(bad=bad), self.assertRaises(ContractSurfaceError):
                validate_rtl_factory(module, {**manifest, "rtl_tuner_gain_contract_version": bad})

    def test_static_runtime_gain_admission_never_loads_sdk_and_refuses_mismatch(self):
        class StaticNative(_Native):
            def RtlExternalFile(self, path, digest):
                return path, digest

            def RtlExternalRuntime(self, library, dependencies):
                return library, dependencies

        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            module = folder / "_sdr_native.mock.pyd"
            module.write_bytes(b"mock native bytes, never imported")
            library = folder / "rtlsdr.dll"
            library.write_bytes(b"mock library bytes, never loaded")
            base = {"rtl_official_compiled": True, "rtl_control_contract_version": 1,
                "artifact_sha256": hashlib.sha256(module.read_bytes()).hexdigest()}
            external = {"library": hashlib.sha256(library.read_bytes()).hexdigest(), "dependencies": {}}
            (folder / "rtl_external_runtime.json").write_text(json.dumps(external), encoding="utf-8")
            native = StaticNative()
            native.__file__ = str(module)
            manifest = folder / "native_build_manifest.json"
            manifest.write_text(json.dumps(base), encoding="utf-8")
            self.assertIsNotNone(qualified_rtl_runtime(native))  # legacy Auto only
            native.RTLSDR_TUNER_GAIN_CONTRACT_VERSION = 1
            self.assertIsNone(qualified_rtl_runtime(native))  # new bridge, old manifest
            for declared in (None, True, "1", 0, 2):
                manifest.write_text(json.dumps({**base, "rtl_tuner_gain_contract_version": declared}), encoding="utf-8")
                with self.subTest(declared=declared):
                    self.assertIsNone(qualified_rtl_runtime(native))
            manifest.write_text(json.dumps({**base, "rtl_tuner_gain_contract_version": 1}), encoding="utf-8-sig")
            admitted = qualified_rtl_runtime(native)
            self.assertIsNotNone(admitted)
            self.assertEqual(admitted.manual_gain_contract_version, 1)
            for observed in (None, True, "1", 0, 2):
                native.RTLSDR_TUNER_GAIN_CONTRACT_VERSION = observed
                with self.subTest(observed=observed):
                    self.assertIsNone(qualified_rtl_runtime(native))
            del native.RTLSDR_TUNER_GAIN_CONTRACT_VERSION
            self.assertIsNone(qualified_rtl_runtime(native))  # manifest cannot invent bridge
            self.assertEqual(native.create_calls, 0)

    def test_full_profile_gain_changes_do_not_collapse_resource_compatibility(self):
        from sdr_monitor.domain.pane_scheduler import CaptureEpochCost, RtlRtbwPaneProfile

        profiles = [RtlRtbwPaneProfile(request(gain), 2_000_000.0,
            CaptureEpochCost(0.0, 0.0, 0.01, 0.0, 0.0)) for gain in (None, 0, 144)]
        self.assertEqual(len({profile.compatibility_key for profile in profiles}), 3)
        for profile in profiles:
            shifted = replace(profile, request_template=replace(profile.request_template,
                center_frequency_hz=160_000_000))
            self.assertEqual(shifted.compatibility_key, profile.compatibility_key)
            self.assertEqual(shifted.request_template.manual_tuner_gain_tenth_db,
                profile.request_template.manual_tuner_gain_tenth_db)

    def test_publication_receipt_does_not_invent_rf_mode_or_cache(self):
        from sdr_monitor.domain.analyzer import RtlTunerGainReceipt

        auto = RtlTunerGainReceipt(RTL_SOURCE_ID, 1, 17, None, None)
        self.assertIsNone(auto.cached_tenth_db)
        for gain in (-99, 0, 144):
            self.assertEqual(RtlTunerGainReceipt(RTL_SOURCE_ID, 1, 17, gain, gain).cached_tenth_db, gain)
        for changes in ({"source_id": ""}, {"config_generation": True}, {"acquisition_epoch": 0},
                {"cached_tenth_db": 0}, {"requested_manual_tenth_db": True, "cached_tenth_db": True},
                {"requested_manual_tenth_db": 144},
                {"requested_manual_tenth_db": 144, "cached_tenth_db": 145}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(auto, **changes)


if __name__ == "__main__":
    unittest.main()
