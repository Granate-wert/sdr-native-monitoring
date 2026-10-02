"""Explicit-path compiled RTL binding proof against the locally built mock C ABI.

Skipped by default. The caller must supply BOTH exact candidate module and
locally compiled mock-SDK paths in a separate process. This is not RF or an
external librtlsdr qualification, and the mock DLL is never packaged.
"""

from __future__ import annotations

import hashlib
import faulthandler
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("SDR_APP07_RTL_BINDING_MODULE") and os.environ.get("SDR_APP07_RTL_MOCK_SDK"),
    "explicit candidate pyd and local mock SDK paths are required",
)
class RtlCompiledBindingTests(unittest.TestCase):
    def test_exact_unbundled_mock_abi_reduced_frame_and_stop(self) -> None:
        faulthandler.enable()

        def phase(name: str) -> None:
            print(f"RTL_BINDING_PHASE={name}", file=sys.stderr, flush=True)

        phase("paths")
        module_path = Path(os.environ["SDR_APP07_RTL_BINDING_MODULE"]).resolve(strict=True)
        mock_path = Path(os.environ["SDR_APP07_RTL_MOCK_SDK"]).resolve(strict=True)
        self.assertEqual(mock_path.name.casefold(), "rtlsdr.dll")
        self.assertTrue(module_path.is_file() and mock_path.is_file())
        module_spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
        assert module_spec is not None and module_spec.loader is not None
        phase("import")
        native = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = native
        module_spec.loader.exec_module(native)
        self.assertIs(native.RTLSDR_OFFICIAL_COMPILED, True)
        self.assertEqual(native.RTLSDR_RX_CONTROL_CONTRACT_VERSION, 1)
        self.assertFalse(native.rtl_process_is_quarantined())
        phase("enumerate")
        digest = hashlib.sha256(mock_path.read_bytes()).hexdigest()
        runtime = native.RtlExternalRuntime(native.RtlExternalFile(str(mock_path), digest), [])
        candidates = native.rtl_enumerate_candidates(runtime)
        self.assertEqual(len(candidates), 1)
        phase("observe")
        observed = native.rtl_observe_single_candidate(runtime)
        self.assertEqual((observed.enumeration_index, observed.serial, observed.tuner_type),
                         (0, "00000001", 5))
        self.assertFalse(observed.direct_sampling or observed.offset_tuning)
        route = native.RtlSessionRoute(observed.manufacturer, observed.product,
                                       observed.serial, observed.tuner_type, 3)
        phase("create")
        owner = native.create_rtl_runtime_control(runtime, 150_000_000, 2_400_000,
            4096, 2048, 8, 6, 16, 4, 7, "rtl-compiled-mock",
            native.DetectorType.PEAK, "", route)
        try:
            phase("readback")
            readback = owner.readback()
            self.assertGreater(readback.session_epoch, 0)
            self.assertEqual((readback.actual_center_hz, readback.actual_sample_rate_hz),
                             (150_000_000, 2_400_000))
            self.assertFalse(readback.tuner_gain_readback_known)
            deadline = time.monotonic() + 2.0
            frame = None
            phase("frame")
            while time.monotonic() < deadline:
                drained = owner.drain_latest_spectrum_frame()
                if drained.frame is not None:
                    frame = drained.frame
                    break
                time.sleep(0.002)
            self.assertIsNotNone(frame)
            assert frame is not None
            self.assertEqual(frame.source.backend_id, "native.librtlsdr.unbundled.cpu.v1")
            self.assertEqual(frame.source.source_id, "rtl-compiled-mock")
            self.assertEqual((frame.config_generation, frame.center_frequency_hz,
                              frame.sample_rate_hz, frame.fft_size),
                             (7, 150_000_000, 2_400_000, 4096))
            self.assertEqual((len(frame.frequencies_hz), len(frame.values)), (4096, 4096))
            phase("metrics")
            metrics = owner.metrics()
            self.assertEqual(metrics.samples_admitted, 24_576)
            self.assertGreater(metrics.dsp.fft_frames_computed, 0)
            with self.assertRaises(Exception):
                native.rtl_observe_single_candidate(runtime)  # lease bars a second probe/open
        finally:
            phase("stop")
            stopped = owner.stop(2000)
            self.assertTrue(stopped.complete())
            self.assertFalse(owner.cleanup_required())
            self.assertFalse(native.rtl_process_is_quarantined())
            phase("complete")

        # Drive the actual Python RTL owner over this exact mock C ABI, not
        # the default product importer or an inferred hardware provision.
        from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
        from sdr_monitor.domain.live import LiveSessionState
        from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
        from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
        from sdr_monitor.services.rtl_capability_provider import (
            RTL_ADAPTER_ID, RTL_SOURCE_ID, RtlCapabilityProvider, RtlRuntimeProvision,
        )

        class Exclusion:
            claimed = False

            def claim_external_analyzer_rx(self, _owner: object) -> None:
                if self.claimed:
                    raise RuntimeError("mock graph already owns RX")
                self.claimed = True

            def release_external_analyzer_rx(self, _owner: object) -> None:
                assert self.claimed
                self.claimed = False

        phase("service_discover")
        module_hash = hashlib.sha256(module_path.read_bytes()).hexdigest()
        runtime_hash = hashlib.sha256((module_hash + digest).encode("ascii")).hexdigest()
        provider = RtlCapabilityProvider(RtlRuntimeProvision(native, runtime, module_hash, runtime_hash))
        provider.discover(startup_only=False)
        inventory = provider.observe_source(RTL_SOURCE_ID)
        binding = inventory.binding_for_source(RTL_SOURCE_ID)
        runtime_snapshot = inventory.runtime_for_adapter(RTL_ADAPTER_ID)
        assert binding is not None and runtime_snapshot is not None
        choice = AnalyzerSourceChoice(binding, runtime_snapshot, "mock RTL USB session", "USB SESSION")
        selection = AnalyzerSourceSelection(3, (choice,), RTL_SOURCE_ID)
        exclusion = Exclusion()
        service = RtlAnalyzerService(native, exclusion, lambda: inventory,
            lambda selected_binding, _runtime: provider.provision_for(selected_binding))
        service.bind_selection(selection)
        request = RtlLiveRequest(150_000_000, 2_400_000,
            detector="peak", source_id=RTL_SOURCE_ID)
        phase("service_stage_start")
        service.stage(RtlConfigurationPatch(request, selection.revision, 0))
        started = service.start()
        try:
            self.assertIs(started.state, LiveSessionState.RUNNING,
                f"compiled mock RTL first fault: {service.first_fault_diagnostic()!r}")
            deadline = time.monotonic() + 2.0
            phase("service_publication")
            while time.monotonic() < deadline:
                current = service.current_snapshot()
                if current.spectrum is not None or current.error is not None:
                    break
                time.sleep(0.002)
            current = service.current_snapshot()
            self.assertIsNone(current.error, f"compiled mock RTL first fault: {service.first_fault_diagnostic()!r}")
            self.assertIsNotNone(current.spectrum)
            assert current.spectrum is not None
            self.assertEqual((current.spectrum.center_frequency_hz, current.spectrum.sample_rate_hz,
                              current.spectrum.fft_size, current.spectrum.hop_size),
                             (150_000_000, 2_400_000, 4096, 2048))
            self.assertEqual(current.spectrum.acquisition_epoch, started.acquisition_epoch)
            assert current.spectrum.numerical_provenance is not None
            self.assertEqual(current.spectrum.numerical_provenance.precision_mode, "reference_f64")
            self.assertTrue(exclusion.claimed)
        finally:
            phase("service_stop")
            released = service.stop()
            self.assertIs(released.state, LiveSessionState.CONNECTED)
            self.assertFalse(released.stop_required)
            self.assertFalse(exclusion.claimed)
            self.assertFalse(native.rtl_process_is_quarantined())
            phase("service_complete")


if __name__ == "__main__":
    unittest.main()
