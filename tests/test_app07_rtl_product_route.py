"""No-I/O product RTL session-route and owner contract tests."""

from __future__ import annotations

import time
import unittest
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import (
    AdapterRuntimeAvailability, AdapterRuntimeSnapshot, DeviceCapabilityBinding,
    DeviceFamily, RtlSessionRouteAssurance, build_device_capability_inventory,
)
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from sdr_monitor.application.analyzer_rtbw_router import AnalyzerRtbwRouter
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from sdr_monitor.services.rtl_capability_provider import (
    RTL_ADAPTER_ID, RTL_SOURCE_ID, RtlCapabilityProvider, RtlRuntimeProvision,
    qualified_rtl_runtime,
)


class _Candidate:
    enumeration_index = 0
    manufacturer = "Generic"
    product = "RTL tuner"
    serial = "00000001"
    tuner_type = 5
    direct_sampling = False
    offset_tuning = False


class _Control:
    def __init__(self, center: int, rate: int) -> None:
        self.center, self.rate = center, rate
        self.stops = 0

    def readback(self):
        return type("Readback", (), {"session_epoch": 17,
            "actual_center_hz": self.center, "actual_sample_rate_hz": self.rate})()

    def drain_latest_spectrum_frame(self):
        return type("Drain", (), {"frame": None, "coalesced_frames": 0})()

    def metrics(self):
        dsp = type("DSP", (), {"fft_frames_computed": 0, "fft_frames_dropped": 0,
                                "output_pending": 0})()
        return type("Metrics", (), {"reader_returned_without_stop": False, "worker_failures": 0,
            "samples_admitted": 0, "blocks_admitted": 0, "dsp": dsp,
            "host_input_samples_dropped": 0, "host_input_blocks_dropped": 0,
            "ready_depth": 0, "presentation_frames_superseded": 0})()

    def stop(self, timeout_ms: int):
        assert timeout_ms == 5000
        self.stops += 1
        return type("Stop", (), {"complete": lambda _self: True})()

    def cleanup_required(self):
        return False


class _FrameControl(_Control):
    """One reduced native-frame seam; IQ never crosses this mock boundary."""

    def __init__(self, center: int, rate: int, generation: int) -> None:
        super().__init__(center, rate)
        self.generation = generation
        self.frames: list[object] = []

    def drain_latest_spectrum_frame(self):
        frame = self.frames.pop(0) if self.frames else None
        return SimpleNamespace(frame=frame, coalesced_frames=0)

    def make_frame(self, sequence: int, *, source_id: str = RTL_SOURCE_ID) -> SimpleNamespace:
        from tests.test_s15_live_rx_bridge import _make_frame

        frame = _make_frame(sequence, center_hz=float(self.center),
            sample_rate_hz=float(self.rate), source_id=source_id,
            config_generation=self.generation)
        frame.source.backend_id = "native.librtlsdr.unbundled.cpu.v1"
        frame.frequencies_hz = self.center + (
            np.arange(frame.fft_size, dtype=np.float64) - frame.fft_size // 2
        ) * (self.rate / frame.fft_size)
        frame.window = "hann"
        frame.detector = "peak"
        frame.precision_mode = "reference_f64"
        frame.calibration_status = "uncalibrated"
        frame.fft_bin_width_hz = self.rate / frame.fft_size
        frame.enbw_hz = 1.5 * frame.fft_bin_width_hz
        frame.nominal_rbw_hz = frame.enbw_hz
        frame.averaging_frames = 1
        frame.calibration_profile_id = ""
        frame.estimated_uncertainty_db = float("nan")
        return frame

    def queue_frame(self, sequence: int, *, source_id: str = RTL_SOURCE_ID) -> None:
        self.frames.append(self.make_frame(sequence, source_id=source_id))


class _Native:
    RTLSDR_OFFICIAL_COMPILED = True
    RTLSDR_RX_CONTROL_CONTRACT_VERSION = 1
    DetectorType = type("Detector", (), {"SAMPLE": "sample", "PEAK": "peak"})

    def __init__(self) -> None:
        self.create_calls = 0
        self.control: _Control | None = None

    def rtl_enumerate_candidates(self, _runtime):
        return [_Candidate()]

    def rtl_observe_single_candidate(self, _runtime):
        return _Candidate()

    def rtl_process_is_quarantined(self):
        return False

    def RtlSessionRoute(self, *args):
        return args

    def create_rtl_runtime_control(self, _runtime, center, rate, *_args):
        self.create_calls += 1
        self.control = _Control(center, rate)
        return self.control


class _Exclusion:
    def __init__(self) -> None:
        self.claimed = False
        self.refuse = False

    def claim_external_analyzer_rx(self, _owner):
        if self.refuse or self.claimed:
            raise RuntimeError("recording or another RX owns graph")
        self.claimed = True

    def release_external_analyzer_rx(self, _owner):
        assert self.claimed
        self.claimed = False


class RtlProductRouteTests(unittest.TestCase):
    @staticmethod
    def _wait(predicate) -> None:
        deadline = time.monotonic() + 4.0
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("bounded four-owner mock publication timeout")
            time.sleep(0.002)

    def test_manifest_bom_or_plain_qualifies_without_loading_sdk(self) -> None:
        class Native(_Native):
            def RtlExternalFile(self, path, digest):
                return (path, digest)

            def RtlExternalRuntime(self, library, dependencies):
                return (library, dependencies)

        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            module = folder / "_sdr_native.mock.pyd"
            library = folder / "rtlsdr.dll"
            module.write_bytes(b"mock-native-contract-only")
            library.write_bytes(b"not-a-vendor-dll")
            native = Native()
            native.__file__ = str(module)
            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()
            build = {"rtl_official_compiled": True, "rtl_control_contract_version": 1,
                     "artifact_sha256": digest(module)}
            external = {"library": digest(library), "dependencies": {}}
            for encoding in ("utf-8", "utf-8-sig"):
                (folder / "native_build_manifest.json").write_text(json.dumps(build), encoding=encoding)
                (folder / "rtl_external_runtime.json").write_text(json.dumps(external), encoding=encoding)
                qualified = qualified_rtl_runtime(native)
                self.assertIsNotNone(qualified)
                self.assertEqual(qualified.runtime[0][0], str(library))
            self.assertEqual(native.create_calls, 0)  # no SDK load/open/Start

    def _selected(self, native: _Native | None = None):
        native = native if native is not None else _Native()
        provision = RtlRuntimeProvision(native, object(), "b" * 64, "c" * 64)
        provider = RtlCapabilityProvider(provision)
        discovered = provider.discover(startup_only=False)
        self.assertIsNone(discovered.binding_for_source(RTL_SOURCE_ID).rtl_session_route)
        inventory = provider.observe_source(RTL_SOURCE_ID)
        binding = inventory.binding_for_source(RTL_SOURCE_ID)
        assert binding is not None
        self.assertIsNone(binding.snapshot)
        self.assertIsNone(binding.calibration_identity)
        self.assertIsInstance(binding.rtl_session_route, RtlSessionRouteAssurance)
        runtime = inventory.runtime_for_adapter(RTL_ADAPTER_ID)
        assert runtime is not None
        choice = AnalyzerSourceChoice(binding, runtime, "RTL selected USB session", "USB SESSION")
        selection = AnalyzerSourceSelection(3, (choice,), RTL_SOURCE_ID)
        return native, provider, inventory, selection

    def test_provider_refresh_invalidates_generic_session_assurance(self) -> None:
        _native, provider, inventory, _selection = self._selected()
        binding = inventory.binding_for_source(RTL_SOURCE_ID)
        assert binding is not None
        self.assertIsNotNone(provider.provision_for(binding))
        fresh = provider.discover(startup_only=False)
        self.assertIsNone(fresh.binding_for_source(RTL_SOURCE_ID).rtl_session_route)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            provider.provision_for(binding)

    def test_same_graph_stage_start_stop_and_recording_refusal(self) -> None:
        native, provider, inventory, selection = self._selected()
        exclusion = _Exclusion()
        service = RtlAnalyzerService(native, exclusion, lambda: inventory,
            lambda binding, _runtime: provider.provision_for(binding))
        service.bind_selection(selection)
        self.assertTrue(service.native_control_available())
        request = RtlLiveRequest(150_000_000, 2_400_000, source_id=RTL_SOURCE_ID)
        staged = service.stage(RtlConfigurationPatch(request, selection.revision, 0))
        self.assertIs(staged.state, LiveSessionState.CONNECTED)
        exclusion.refuse = True
        with self.assertRaisesRegex(RuntimeError, "recording"):
            service.start()
        self.assertEqual(native.create_calls, 0)  # before any SDK factory call
        exclusion.refuse = False
        started = service.start()
        self.assertIs(started.state, LiveSessionState.RUNNING)
        self.assertEqual(started.acquisition_epoch, 17)
        self.assertEqual(started.active_source_id, RTL_SOURCE_ID)
        self.assertTrue(exclusion.claimed)
        time.sleep(0.03)
        stopped = service.stop()
        self.assertIs(stopped.state, LiveSessionState.CONNECTED)
        self.assertFalse(stopped.stop_required)
        self.assertFalse(exclusion.claimed)
        assert native.control is not None
        self.assertEqual(native.control.stops, 1)

    def test_wrong_reduced_native_metadata_refuses_before_live_publication(self) -> None:
        class FrameNative(_Native):
            def create_rtl_runtime_control(self, _runtime, center, rate, *_args):
                self.create_calls += 1
                self.control = _FrameControl(center, rate, int(_args[6]))
                return self.control

        cases = (
            ("center", "center_frequency_hz", 150_000_001),
            ("rate", "sample_rate_hz", 2_048_000),
            ("fft", "fft_size", 2048),
            ("hop", "hop_size", 1024),
            ("producer", "backend_id", "native.rtlsdr.injected.cpu.v1"),
            ("missing producer", "backend_id", None),
            ("window", "window", "rectangular"),
            ("detector", "detector", "sample"),
            ("precision", "precision_mode", "fast_f32"),
            ("averaging", "averaging_frames", 2),
            ("unit", "unit", SimpleNamespace(name="DBM_BIN")),
            ("short frequencies", "frequencies_hz", np.zeros(2048, dtype=np.float64)),
            ("short values", "values", np.zeros(2048, dtype=np.float32)),
            ("rank2 frequencies", "frequencies_hz", np.zeros((1, 4096), dtype=np.float64)),
            ("rank2 values", "values", np.zeros((1, 4096), dtype=np.float32)),
        )
        for name, field, bad_value in cases:
            with self.subTest(name=name):
                native = FrameNative()
                _native, provider, inventory, selection = self._selected(native)
                exclusion = _Exclusion()
                service = RtlAnalyzerService(native, exclusion, lambda: inventory,
                    lambda binding, _runtime: provider.provision_for(binding))
                service.bind_selection(selection)
                request = RtlLiveRequest(150_000_000, 2_400_000,
                    detector="peak", source_id=RTL_SOURCE_ID)
                service.stage(RtlConfigurationPatch(request, selection.revision, 0))
                self.assertIs(service.start().state, LiveSessionState.RUNNING)
                control = native.control
                assert isinstance(control, _FrameControl)
                frame = control.make_frame(1)
                if field == "backend_id":
                    frame.source.backend_id = bad_value
                else:
                    setattr(frame, field, bad_value)
                frequencies = frame.frequencies_hz
                values = frame.values
                control.frames.append(frame)
                try:
                    self._wait(lambda: service.current_snapshot().error is not None)
                    fault = service.first_fault_diagnostic()
                    self.assertIsNotNone(fault)
                    assert fault is not None
                    self.assertEqual(fault[0], "reduced_publication")
                    self.assertIsInstance(fault[1], ValueError)
                    failed = service.current_snapshot()
                    self.assertIs(failed.state, LiveSessionState.ERROR)
                    self.assertIsNone(failed.spectrum)
                    self.assertTrue(failed.stop_required)
                    self.assertIs(frame.frequencies_hz, frequencies)
                    self.assertIs(frame.values, values)
                finally:
                    stopped = service.stop()
                    self.assertIs(stopped.state, LiveSessionState.CONNECTED)
                    self.assertFalse(stopped.stop_required)
                    self.assertFalse(exclusion.claimed)
                    self.assertEqual(control.stops, 1)

    def test_runtime_unavailable_never_admits_generic_route(self) -> None:
        _native, _provider, inventory, selection = self._selected()
        binding = selection.selected.binding
        unavailable = AdapterRuntimeSnapshot(RTL_ADAPTER_ID, DeviceFamily.RTL_SDR,
            AdapterRuntimeAvailability.UNAVAILABLE, "mock-runtime-absent")
        denied = build_device_capability_inventory((), bindings=(binding,), runtimes=(unavailable,))
        from sdr_monitor.services.source_capability_admission import admit_source_request
        request = RtlLiveRequest(150_000_000, 2_400_000, source_id=RTL_SOURCE_ID)
        self.assertFalse(admit_source_request(denied, RTL_SOURCE_ID, "rtbw", request).accepted)
        self.assertIsNotNone(inventory.binding_for_source(RTL_SOURCE_ID))

    def test_pane_candidate_visible_while_default_graph_stays_ad_selected(self) -> None:
        native, provider, inventory, _selection = self._selected()
        rtl_binding = inventory.binding_for_source(RTL_SOURCE_ID)
        rtl_runtime = inventory.runtime_for_adapter(RTL_ADAPTER_ID)
        assert rtl_binding is not None and rtl_runtime is not None
        rtl_choice = AnalyzerSourceChoice(rtl_binding, rtl_runtime, "RTL candidate", "USB SESSION")
        ad_binding = DeviceCapabilityBinding("ad-default", DeviceFamily.AD936X, "ad.mock")
        ad_choice = AnalyzerSourceChoice(ad_binding, None, "AD default", "USB")
        selection = AnalyzerSourceSelection(9, (ad_choice, rtl_choice), ad_choice.device_id)
        sources = type("Sources", (), {"current": lambda self: selection})()
        owner = RtlAnalyzerService(native, _Exclusion(), lambda: inventory,
            lambda binding, _runtime: provider.provision_for(binding))
        router = AnalyzerRtbwRouter(object(), sources, None, owner)
        self.assertTrue(router.rtl_candidate_stage_available(RTL_SOURCE_ID, 9))
        self.assertFalse(router.rtl_controls_available(RTL_SOURCE_ID, 9))
        self.assertFalse(router.rtl_candidate_stage_available(RTL_SOURCE_ID, 8))
        self.assertFalse(router.rtl_candidate_stage_available("foreign", 9))

    def test_four_independent_graphs_rtl_pane4_delivery_stop_keeps_peers(self) -> None:
        """Four actual app graph owners, mock native/SDK/serial only, never RF."""
        from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
        from sdr_monitor.domain.live import LiveConfiguration
        from sdr_monitor.domain.pane_scheduler import (
            CaptureEpochCost, HackrfRtbwPaneProfile, PaneCaptureProfile,
            PaneLayoutSlot, RtlRtbwPaneProfile, TinySaTracePaneProfile, compile_pane_layout,
        )
        from sdr_monitor.domain.receiver_topology import (
            AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection,
            ReceiverEndpoint, SpectrumTraceEndpoint,
        )
        from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
        from sdr_monitor.services.native_live import NativeLiveSessionService
        from sdr_monitor.services.pane_resource_session import PaneResourceError
        from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
        from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
        from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
        from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
        from sdr_monitor.ui.v2_pane_composition import compose_v2_pane_resource_session
        from tests.test_s15_live_rx_bridge import _make_frame
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_graph
        from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_graph
        from tests.ui_v2.test_app07_three_concrete_owners import (
            _ObservedReadbackNative, _UnusedSweep,
        )
        from tests.test_app07_shared_capture_schedule import pane

        class RtlGraphNative(_ObservedReadbackNative, _Native):
            DetectorType = _Native.DetectorType

            def __init__(self) -> None:
                _ObservedReadbackNative.__init__(self, serial="rtl-graph-pluto-unused",
                                                 uri="ip:rtl-graph-ad-unused.local")
                _Native.__init__(self)
                self.DetectorType = _Native.DetectorType  # AD fake sets a SAMPLE-only instance enum.

            def create_rtl_runtime_control(self, _runtime, center, rate, *_args):
                self.create_calls += 1
                self.control = _FrameControl(center, rate, int(_args[6]))
                return self.control

        cost = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)
        ad_native = _ObservedReadbackNative(serial="four-pane-pluto", uri="ip:four-pane.local")
        ad_service = NativeLiveSessionService(ad_native)
        ad_catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(ad_service),),
            control_transaction=ad_service.capability_control_transaction)
        ad_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ad_service, analyzer_display=_UnusedSweep(), device_catalog=ad_catalog))
        ad_choice = ad_graph.live.discover()[0]
        ad_graph.live.select_device(ad_choice.device_id)
        ad_graph.live.apply_configuration(LiveConfiguration(
            center_hz=104e6, sample_rate_hz=20e6, analog_bandwidth_hz=10e6,
            gain_db=20, fft_size=4096, detector="sample",
            persistence_enabled=False, persistence_mode="disabled"))
        hf = hackrf_graph()
        ts = tinysa_graph()
        hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        ts_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument))
        hf_graph.live.discover()
        hf_graph.live.select_device("source-hackrf")
        ts_choice = ts_graph.live.discover()[0]
        ts_graph.live.select_device(ts_choice.device_id)
        ts_selection = ts_graph.live.current_source_selection()
        assert ts_selection is not None and ts_selection.selected is not None

        rtl_native = RtlGraphNative()
        rtl_live = NativeLiveSessionService(rtl_native)
        rtl_provider = RtlCapabilityProvider(RtlRuntimeProvision(
            rtl_native, object(), "b" * 64, "c" * 64))
        rtl_catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(rtl_live), rtl_provider),
            control_transaction=rtl_live.capability_control_transaction)
        rtl_owner = RtlAnalyzerService(rtl_native, rtl_live, rtl_catalog.snapshot,
                                      rtl_catalog.rtl_provision_for)
        rtl_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=rtl_live, device_catalog=rtl_catalog, analyzer_rtl=rtl_owner))
        rtl_choices = rtl_graph.live.discover()
        self.assertIn(RTL_SOURCE_ID, tuple(value.device_id for value in rtl_choices))
        rtl_graph.live.select_device(RTL_SOURCE_ID)
        rtl_selection = rtl_graph.live.current_source_selection()
        assert rtl_selection is not None and rtl_selection.selected is not None
        self.assertIsNotNone(rtl_selection.selected.binding.rtl_session_route)

        groups = (
            AcquisitionGroup("ad:group", "ad:physical", (
                ReceiverEndpoint("ad:rx1", ad_choice.device_id, "ad:physical", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("hf:group", "hf:physical", (
                ReceiverEndpoint("hf:rx1", "source-hackrf", "hf:physical", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("ts:group", "ts:physical", (
                SpectrumTraceEndpoint("ts:trace", ts_selection.selected.device_id, "ts:physical"),)),
            AcquisitionGroup("rtl:group", "rtl:physical", (
                ReceiverEndpoint("rtl:rx1", RTL_SOURCE_ID, "rtl:physical", ReceiverChainSelection.RX1),)),
        )
        slots = (
            PaneLayoutSlot(1, replace(pane("ad-pane", "ad:rx1", 100e6, 108e6,
                ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="ad")),
            PaneLayoutSlot(2, replace(pane("hf-pane", "hf:rx1", 140e6, 148e6,
                ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="hf")),
            PaneLayoutSlot(3, replace(pane("ts-pane", "ts:trace", 200e6, 210e6,
                ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="ts")),
            PaneLayoutSlot(4, replace(pane("rtl-pane", "rtl:rx1", 149.5e6, 150.5e6,
                ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="rtl")),
        )
        profiles = {
            "ad": PaneCaptureProfile(20e6, 10e6, "manual", 20.0,
                4096, 2048, "hann", "sample", None, 10e6, cost),
            "hf": HackrfRtbwPaneProfile(HackrfLiveRequest(
                144e6, 20e6, 15_000_000, 16, 20, fft_size=4096,
                hop_size=2048, detector="peak", source_id="source-hackrf"), 10e6, cost),
            "ts": TinySaTracePaneProfile(3, 11e6, "ultra-default", cost,
                request_template=TinySaSweepRequest(ts_selection.selected,
                    ts_selection.revision, 200_000_000, 210_000_000, 3)),
            "rtl": RtlRtbwPaneProfile(RtlLiveRequest(150_000_000, 2_400_000,
                detector="peak", source_id=RTL_SOURCE_ID), 1_200_000, cost),
        }
        layout = compile_pane_layout(slots, groups, profiles)
        self.assertEqual(layout.empty_slots, ())
        leases = ReceiverLeaseManager(max_active_resources=4)
        session = compose_v2_pane_resource_session(layout, groups, {
            "ad:physical": ad_graph, "hf:physical": hf_graph,
            "ts:physical": ts_graph, "rtl:physical": rtl_graph,
        }, leases)
        assert session is not None
        try:
            session.apply()
            for resource in ("ad:physical", "hf:physical", "ts:physical", "rtl:physical"):
                try:
                    session.start_resource(resource)
                except Exception as error:
                    self.fail(f"{resource} mock owner Start failed: {error!r}; "
                        f"failure={getattr(error, 'failure', None)!r}; "
                        f"rtl_first_fault={rtl_owner.first_fault_diagnostic()!r}; "
                        f"rtl_snapshot={rtl_graph.live.current_snapshot()!r}; "
                        f"rtl_analyzer={rtl_graph.live.analyzer_state!r}")
            self.assertEqual(leases.active_resource_count, 4)
            self.assertEqual(rtl_native.create_calls, 1)
            assert isinstance(rtl_native.control, _FrameControl)
            rtl_native.control.queue_frame(1)
            self._wait(lambda: rtl_graph.live.current_snapshot().spectrum is not None
                       or rtl_graph.live.current_snapshot().error is not None)
            self.assertIsNone(rtl_graph.live.current_snapshot().error,
                f"RTL mock reduced-publication fault={rtl_owner.first_fault_diagnostic()!r}; "
                f"snapshot={rtl_graph.live.current_snapshot()!r}")
            ad_center = ad_service.latest_snapshot().applied.applied.center_hz
            ad_frame = _make_frame(1, center_hz=ad_center, sample_rate_hz=20e6,
                                   source_id=ad_choice.device_id, config_generation=1)
            ad_frame.frequencies_hz = ad_center + (
                np.arange(4096) - 2048) * (20e6 / 4096)
            ad_native.engines[0].frames.append(ad_frame)
            self._wait(lambda: ad_service.latest_snapshot().spectrum is not None)
            self._wait(lambda: hf.hackrf.current_snapshot().spectrum is not None)
            self._wait(lambda: ts.instrument.poll_latest().line is not None)
            deliveries = {resource: session.poll_resource(resource) for resource in (
                "ad:physical", "hf:physical", "ts:physical", "rtl:physical")}
            self.assertEqual(tuple(deliveries), (
                "ad:physical", "hf:physical", "ts:physical", "rtl:physical"))
            self.assertEqual(tuple(value[0].pane_id for value in deliveries.values()),
                             ("ad-pane", "hf-pane", "ts-pane", "rtl-pane"))
            self.assertEqual(tuple(value[0].bundle.unit for value in deliveries.values()),
                             ("dBFS/bin", "dBFS/bin", "dBm", "dBFS/bin"))
            self.assertEqual(deliveries["rtl:physical"][0].bundle.spectrum.acquisition_epoch, 17)
            rtl_spectrum = deliveries["rtl:physical"][0].bundle.spectrum
            self.assertEqual((rtl_spectrum.sample_rate_hz, rtl_spectrum.fft_size,
                              rtl_spectrum.hop_size), (2_400_000, 4096, 2048))
            assert rtl_spectrum.numerical_provenance is not None
            self.assertEqual(rtl_spectrum.numerical_provenance.precision_mode, "reference_f64")
            peer_epochs = (ad_service.latest_snapshot().acquisition_epoch,
                           hf.hackrf.current_snapshot().acquisition_epoch,
                           ts.instrument.poll_latest().line.epoch)
            rtl_native.control.queue_frame(2, source_id="wrong-mock-source")
            self._wait(lambda: rtl_graph.live.current_snapshot().error is not None)
            with self.assertRaisesRegex(PaneResourceError, "publication failed"):
                session.poll_resource("rtl:physical")
            self.assertEqual(session.stop_selected("rtl-pane"), ("rtl-pane",))
            self.assertEqual(leases.active_resource_count, 3)
            self.assertFalse(rtl_graph.live.is_running())
            self.assertTrue(ad_service.is_running() and hf.hackrf.is_running())
            self.assertTrue(ts.instrument.stop_required)
            self.assertEqual(peer_epochs[:2], (ad_service.latest_snapshot().acquisition_epoch,
                                               hf.hackrf.current_snapshot().acquisition_epoch))
            self.assertEqual(ts.instrument.poll_latest().line.epoch, peer_epochs[2])
            next_ad_frame = _make_frame(2, center_hz=ad_center, sample_rate_hz=20e6,
                                        source_id=ad_choice.device_id, config_generation=1)
            next_ad_frame.frequencies_hz = ad_frame.frequencies_hz.copy()
            ad_native.engines[0].frames.append(next_ad_frame)
            self._wait(lambda: ad_service.latest_snapshot().spectrum.sequence == 2)
            self.assertEqual(tuple(value.pane_id for value in session.poll_resource("ad:physical")),
                             ("ad-pane",))
        finally:
            self.assertEqual(session.stop_all(), ())
            self.assertEqual(leases.active_resource_count, 0)
            for graph in (ad_graph, hf_graph, ts_graph, rtl_graph):
                graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
