"""Actual AD application lease/factory/native IIO MOCK; no physical fallback."""
from dataclasses import replace
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.native_test_dependencies import explicit_native_dependencies
from tests.test_app07_paired_sweep_binding import MODULE, MOCK, ROOT


@explicit_native_dependencies
def run_case(path: str, case: str) -> None:
    from sdr_monitor.domain import BackendKind, LiveConfiguration
    from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
    from sdr_monitor.domain.paired_live import PairedLiveRequest
    from sdr_monitor.domain.paired_sweep import PairedSweepRequest
    from sdr_monitor.domain.processing_policy import HostDcMode, SdrProcessingPolicyV1
    from sdr_monitor.domain.sweep_processing import AdSweepProcessingContextV1
    from sdr_monitor.domain.continuous_sweep_geometry import sweep_step_geometry
    from sdr_monitor.domain.source_processing import RfValueAuthority
    from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
    from sdr_monitor.services.native_live import NativeLiveSessionService
    from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
    from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
    from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
    from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep

    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    assert spec is not None and spec.loader is not None
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
        control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, device_catalog=catalog, analyzer_display=_UnusedSweep()))
    factory = display = None

    def refuses(operation):
        try:
            operation()
        except (ValueError, RuntimeError, TypeError, native.ConfigurationError):
            return
        raise AssertionError("foreign config/context must refuse")

    try:
        source = graph.live.discover(startup=True)[0].device_id
        graph.live.select_device(source)
        policy = SdrProcessingPolicyV1(dc_mode=HostDcMode.OFF if case.endswith("off") else HostDcMode.BLOCK_MEAN)
        selected = graph.live.apply_configuration(LiveConfiguration(center_hz=2450e6,
            sample_rate_hz=61_440_000., analog_bandwidth_hz=56e6, fft_size=4096,
            gain_db=20., averaging_frames=1, backend=BackendKind.CPU, processing_policy=policy))
        assert selected.applied is not None and selected.error is None
        sweep = ContinuousSweepPlanRequest(2400e6, 2480e6, epoch=17,
            acquisition_buffer_samples=8192, segment_frame_timeout_ms=2000,
            analysis_bins_per_usable_window=2048 if "analysis" in case else 0)
        writes, contexts = hooks.mock_iio_rf_mutation_calls(), hooks.mock_iio_created_contexts()
        if case == "legacy-refusal":
            with patch.object(native, "PLUTO_SWEEP_PRODUCT_RESERVATION_PROTOCOL_VERSION", 0):
                refuses(service.acquire_native_continuous_sweep_lease)
            assert not service._sweep_lease_active
            assert (writes, contexts) == (hooks.mock_iio_rf_mutation_calls(), hooks.mock_iio_created_contexts())
            return
        if case == "sequential-refusal":
            refuses(service.acquire_native_sweep_lease)
            assert not service._sweep_lease_active
            assert (writes, contexts) == (hooks.mock_iio_rf_mutation_calls(), hooks.mock_iio_created_contexts())
            return
        paired = case.startswith("paired")
        if paired:
            selection = graph.live.current_source_selection()
            assert selection is not None and selected.device is not None
            pair = PairedLiveRequest(selected.device.device_id, str(selected.session_id),
                selected.device.capabilities.receiver_topology, selected.applied.applied, "processed-left", "processed-right")
            request = PairedSweepRequest("processed-resource", pair, sweep, selection.revision, selected)
            if "pane" in case:
                from tests.test_app07_paired_sweep_capture_job import run_pane_case
                from sdr_monitor.domain.analyzer import bundles_from_paired_sweep
                observed = []
                def observe(publication):
                    for frame in (publication.primary, publication.secondary):
                        assert type(frame.processing_context) is AdSweepProcessingContextV1
                        assert frame.processing_context.plan.policy == policy
                    bundles = bundles_from_paired_sweep(publication)
                    assert len(bundles) == 2 and all(bundle.paired_sweep is publication for _, bundle in bundles)
                    observed.append(publication)
                    return bundles
                with patch("sdr_monitor.services.ad936x_paired_sweep_pane_owner.bundles_from_paired_sweep",
                           side_effect=observe):
                    run_pane_case(graph, request, "pane-workflow", hooks)
                assert observed
                return
            factory = NativeContinuousSweepPlanFactory.from_paired_application(graph.live, request)
            config = factory.build_paired()
            display = factory.create_coordinator()
        else:
            factory = NativeContinuousSweepPlanFactory.from_native_live(service)
            config = factory.build(sweep)
            display = factory.create_display_service()
        if "cancel" in case:
            prefix = "prefix" in case
            center = sweep_step_geometry(sweep, 1 if prefix else 0).center_hz
            os.environ["SDR_MOCK_LIBIIO_LO_WRITE_FAIL_AT_HZ"] = str(int(center))
            if paired:
                display.configure_paired(config)
                display.start()
                raw = display._owner
            else:
                display.start(config)
                raw = display._coordinator
            deadline = time.monotonic() + 5
            while raw.state() != native.EngineState.ERROR and time.monotonic() < deadline:
                time.sleep(.002)
            assert raw.state() == native.EngineState.ERROR
            display.stop()
            if paired:
                archive = display.poll_retired_archive()
                assert archive.terminals
                frames = (archive.terminals[-1].primary, archive.terminals[-1].secondary)
            else:
                packet = display.poll_latest()
                assert packet.line is not None
                frames = (packet.line,)
            for frame in frames:
                context = frame.processing_context
                assert context is not None and frame.missing_segment_indices
                assert bool(frame.segment_acquisition) == prefix
                assert context.rf_authority is (RfValueAuthority.READBACK if prefix else RfValueAuthority.UNKNOWN)
                if prefix:
                    assert len(frame.segment_acquisition) == 1
            return
        previous = None
        for iteration in range(2):
            if paired:
                display.configure_paired(config)
                display.start()
            else:
                # Single coordinator epoch comes from the actual new plan.
                if iteration:
                    display.close()
                    factory.close()  # Release the actual registered owner before another lease.
                    factory = NativeContinuousSweepPlanFactory.from_native_live(service)
                    config = factory.build(replace(sweep, epoch=18))
                    display = factory.create_display_service()
                if case == "single-config-refusal":
                    altered = factory.build(replace(sweep, stop_hz=2490e6))
                    refuses(lambda: display.start(altered))
                    assert hooks.mock_iio_rf_mutation_calls() == writes
                    return
                display.start(config)
            progress_seen = terminal_seen = False
            current_epoch = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if paired:
                    progress = display.poll_observed_progress()
                    packets = ((progress,) if progress is not None else ()) + display.poll_observed_lines()
                    groups = [(packet.primary, packet.secondary) for packet in packets]
                else:
                    packet = display.poll_latest()
                    groups = [(frame,) for frame in (packet.progress, packet.line) if frame is not None]
                for frames in groups:
                    for index, frame in enumerate(frames):
                        context = frame.processing_context
                        assert type(context) is AdSweepProcessingContextV1
                        assert context.plan.policy == policy
                        assert context.plan.session_id == str(selected.session_id)
                        assert context.plan.receiver_id == ("RX1" if index == 0 else "RX2")
                        assert context.layer_ready == frame.layer_ready
                        assert frame.segment_acquisition and all(r.processing_metadata for r in frame.segment_acquisition)
                        assert all(r.processing_metadata.numerical_provenance.processing_recipe.policy == policy
                                   for r in frame.segment_acquisition)
                        if current_epoch is None:
                            current_epoch = frame.epoch
                        assert frame.epoch == current_epoch
                        if case.endswith("negative"):
                            refuses(lambda: replace(frame, unit="dBm"))
                            refuses(lambda: replace(frame, source_id="foreign-source"))
                            refuses(lambda: replace(frame, epoch=frame.epoch + 1))
                            refuses(lambda: replace(frame, receiver_id="RX2" if index == 0 else "RX1"))
                            refuses(lambda: replace(frame, processing_context=replace(context,
                                plan=replace(context.plan, sample_rate_hz=30_720_000.))))
                            refuses(lambda: replace(context, plan=replace(context.plan, session_id="foreign")))
                            first = frame.segment_acquisition[0]
                            metadata = first.processing_metadata
                            refuses(lambda: replace(frame, segment_acquisition=(replace(first,
                                processing_metadata=replace(metadata, center_frequency_hz=metadata.center_frequency_hz + 1.)),
                                *frame.segment_acquisition[1:])))
                        if hasattr(frame, "revision"):
                            assert not terminal_seen  # Observe a genuine progressive prefix before completion.
                            progress_seen = True
                        else:
                            terminal_seen = True
                    if paired:
                        assert frames[0].epoch == frames[1].epoch
                        assert frames[0].processing_context.layer_ready.owner_run_id == frames[1].processing_context.layer_ready.owner_run_id
                if progress_seen and terminal_seen:
                    break
                time.sleep(.002)
            assert progress_seen and terminal_seen, (case, progress_seen, terminal_seen)
            if previous is not None:
                assert current_epoch > previous
            previous = current_epoch
            display.stop()
            if paired:
                archive = display.poll_retired_archive()
                for item in archive.terminals:
                    assert item.primary.processing_context is not None and item.secondary.processing_context is not None
                assert all(j.native_stop_confirmed for j in display.layer_journal_snapshots())
            else:
                final = display.poll_latest()
                if final.line is not None:
                    assert final.line.processing_context is not None
                assert display.layer_journal_snapshot().native_stop_confirmed
    finally:
        if display is not None and not case.startswith("paired"):
            display.close()
        if factory is not None:
            factory.close()
        service.close_live()
        catalog.close()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    print(f"AD Sweep original owner {case} PASS; MOCK only, no RF claim")


@unittest.skipUnless(MODULE and MOCK and Path(MOCK).is_file(), "requires explicit matching native/IIO MOCK")
class AdSweepProcessingNativeOwnerTests(unittest.TestCase):
    def run_native(self, case):
        environment = dict(os.environ, LIBIIO_DLL_PATH=str(Path(MOCK).resolve(strict=True)),
            SDR_MOCK_LIBIIO_CONTEXT_NAME="usb", SDR_MOCK_LIBIIO_BACKEND_URI="usb:2.42.5",
            SDR_MOCK_LIBIIO_TOPOLOGY_DUAL="1", SDR_MOCK_LIBIIO_REFILL_DELAY_MS="1")
        result = subprocess.run([sys.executable, "-c",
            "from tests.test_ad_sweep_processing_native_owner import run_case; import sys; run_case(sys.argv[1],sys.argv[2])",
            MODULE, case], cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_single_off_original_owner(self): self.run_native("single-off")
    def test_single_block_mean_original_owner(self): self.run_native("single-processed")
    def test_paired_off_original_owner(self): self.run_native("paired-off")
    def test_paired_block_mean_original_owner(self): self.run_native("paired-processed")
    def test_single_foreign_context_refused(self): self.run_native("single-negative")
    def test_paired_foreign_context_refused(self): self.run_native("paired-negative")
    def test_foreign_single_native_plan_refused_before_configure(self): self.run_native("single-config-refusal")
    def test_legacy_code_refuses_processed_lease_without_rf(self): self.run_native("legacy-refusal")
    def test_legacy_sequential_sweep_still_refuses_processed_lease(self): self.run_native("sequential-refusal")
    def test_single_cancel_retains_processed_prefix(self): self.run_native("single-cancel-prefix")
    def test_paired_cancel_retains_both_processed_prefixes(self): self.run_native("paired-cancel-prefix")
    def test_single_zero_prefix_cancel_keeps_rf_unknown(self): self.run_native("single-cancel-empty")
    def test_paired_zero_prefix_cancel_keeps_rf_unknown(self): self.run_native("paired-cancel-empty")
    def test_single_analysis_n_context_keeps_physical_fft(self): self.run_native("single-analysis")
    def test_paired_analysis_n_context_keeps_physical_fft(self): self.run_native("paired-analysis")
    def test_actual_paired_capture_job_delivers_both_off_contexts(self): self.run_native("paired-pane-off")
    def test_actual_paired_capture_job_delivers_both_block_mean_contexts(self): self.run_native("paired-pane-processed")
