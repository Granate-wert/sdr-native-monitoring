"""SAME application selection -> paired native plan/owner; compiled MOCK only."""
from dataclasses import replace
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
from types import SimpleNamespace
import unittest

from tests.native_test_dependencies import explicit_native_dependencies
from tests.test_app07_paired_sweep_binding import MODULE, MOCK, ROOT


@explicit_native_dependencies
def run_case(path: str, case: str) -> None:
    import ctypes
    from unittest.mock import patch

    from sdr_monitor.domain import BackendKind, LiveConfiguration, RecordingOptions
    from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
    from sdr_monitor.domain.paired_live import PairedLiveRequest
    from sdr_monitor.domain.paired_sweep import PairedSweepRequest
    from sdr_monitor.domain.sweep_statistics import SweepStatisticsSettings
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
    assert Path(native.__file__).resolve() == Path(path).resolve()
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
        control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, device_catalog=catalog, analyzer_display=_UnusedSweep()))
    factory = None

    def refuses(operation):
        try:
            operation()
        except (ValueError, RuntimeError, TypeError, native.ConfigurationError):
            return
        raise AssertionError("operation must refuse before ownership/RF mutation")

    def refuses_admission(intent):
        nonlocal factory
        try:
            factory = NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent)
        except (ValueError, RuntimeError, TypeError, native.ConfigurationError) as error:
            if case == "budget":
                assert "aggregate 128MiB" in str(error), error
            return
        raise AssertionError("paired admission must refuse before owning/opening")

    try:
        source = graph.live.discover(startup=True)[0].device_id
        graph.live.select_device(source)
        profile = LiveConfiguration(center_hz=2450e6, sample_rate_hz=61_440_000.,
            analog_bandwidth_hz=56e6, fft_size=4096, gain_db=20., averaging_frames=1,
            backend=BackendKind.CPU)
        selected = graph.live.apply_configuration(profile)
        assert selected.error is None and selected.applied is not None
        selection = graph.live.current_source_selection()
        assert selection is not None and selection.selected is not None
        device = selected.device
        assert device is not None
        topology = device.capabilities.receiver_topology
        assert topology is not None
        stats = SweepStatisticsSettings(window_passes=8, power_bins=8, density_columns=64,
                                        max_payload_bytes=128 * 1024 * 1024)
        sweep = ContinuousSweepPlanRequest(2400e6, 2480e6, epoch=17,
            acquisition_buffer_samples=8192, segment_frame_timeout_ms=2000, statistics=stats)
        writes = hooks.mock_iio_rf_mutation_calls()
        contexts = hooks.mock_iio_created_contexts()

        def request():
            pair = PairedLiveRequest(device.device_id, str(selected.session_id), topology,
                selected.applied.applied, "product-left", "product-right")
            return PairedSweepRequest("product-resource", pair, sweep, selection.revision, selected)

        if case in ("single", "empty-serial"):
            refuses(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, request()))
        else:
            intent = request()
            if case == "stale-revision":
                graph.live.select_device(source)  # Real re-observation/revision, not a forged request.
                writes = hooks.mock_iio_rf_mutation_calls()
                contexts = hooks.mock_iio_created_contexts()
                refuses(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent))
            elif case == "armed":
                service.arm_native_recording(RecordingOptions(output_path="unused-paired-sweep"))
                refuses(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent))
                assert service._native_recording_armed is not None
                service.stop_native_recording()
            elif case == "rtbw":
                graph.live.stage_paired_rtbw(intent.pair)
                refuses(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent))
                graph.live.clear_paired_rtbw()
            elif case == "protocol":
                with patch.object(native, "PLUTO_PAIRED_SWEEP_REDUCED_PROTOCOL_VERSION", 0):
                    refuses(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent))
            elif case == "budget":
                large = replace(stats, power_bins=256, density_columns=2048)
                excessive = replace(intent, sweep=replace(sweep, statistics=large, output_queue_capacity=6))
                # EACH scalar plan fits; combined native pair must refuse.
                NativeContinuousSweepPlanFactory.preflight_profile(profile, excessive.sweep)
                refuses_admission(excessive)
            else:
                factory = NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent)
                config = factory.build_paired()
                assert config.resource_id == intent.resource_id
                assert config.primary.segments[0].fixed_band.device.source_id == "product-left"
                assert config.secondary.segments[0].fixed_band.device.source_id == "product-right"
                assert len(config.primary.segments) == len(config.secondary.segments) == 3
                assert config.primary.statistics is not None and config.secondary.statistics is not None
                assert hooks.mock_iio_rf_mutation_calls() == writes
                assert hooks.mock_iio_created_contexts() == contexts
                if case == "changed-after-lease":
                    original = graph.sources._state
                    graph.sources._state = replace(original, revision=original.revision + 1)
                    try:
                        refuses(factory.create_coordinator)
                        refuses(factory.build_paired)
                        assert hooks.mock_iio_created_contexts() == contexts
                    finally:
                        graph.sources._state = original
                    factory.close()
                    factory.close()  # No ABA app reservation after completed release.
                    refuses(factory.build_paired)
                    assert graph.live._pane_control_claim is None
                    factory = None
                elif case == "cleanup":
                    from sdr_monitor.services.ad936x_identity_admission import create_identity_bound_owner
                    attempts = []

                    def cleanup(owner):
                        attempts.append(owner)
                        if len(attempts) == 1:
                            raise RuntimeError("injected cleanup failure")
                        owner.disconnect()

                    owner = factory._construct_owner(lambda: create_identity_bound_owner(
                        native, "NativeContinuousSweepCoordinator", factory._lease.source.context_uri,
                        3000, expected_serial="MOCK"), cleanup)
                    assert hooks.mock_iio_created_contexts() == contexts + 1
                    refuses(factory.close)
                    assert graph.live._pane_control_claim is not None and service._sweep_lease_active
                    assert service._sweep_native_owner[0] is owner
                    refuses(factory.create_coordinator)
                    refuses(lambda: graph.live.select_device(source))
                    refuses(graph.live.start)
                    factory.close()
                    assert attempts == [owner, owner]
                    assert graph.live._pane_control_claim is None and not service._sweep_lease_active
                    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                    factory = None
                    return
                elif case.startswith("race-"):
                    coordinator = factory.create_coordinator()
                    coordinator.configure_paired(config)
                    before_entry = threading.Event()
                    resume = threading.Event()
                    original = graph.live.pane_control_transaction

                    @contextmanager
                    def interleaved(claim=None, **kwargs):
                        if threading.current_thread().name.startswith("old-lease"):
                            before_entry.set()
                            assert resume.wait(3), "release did not unblock old operation"
                        with original(claim, **kwargs):
                            yield

                    operation = coordinator.start if case == "race-control" else coordinator.stop
                    with patch.object(graph.live, "pane_control_transaction", interleaved):
                        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-lease") as pool:
                            pending = pool.submit(operation)
                            try:
                                assert before_entry.wait(3), "old operation did not reach entry gate"
                                factory.close()
                            finally:
                                resume.set()
                            refuses(lambda: pending.result(timeout=3))
                    assert graph.live._pane_control_claim is None, "stale transaction recreated old claim"
                    assert not service._sweep_lease_active
                    assert hooks.mock_iio_rf_mutation_calls() == writes
                    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                    factory = None
                    return
                elif case in ("single-escape", "foreign-pair", "stale-start", "running-stale"):
                    coordinator = factory.create_coordinator()
                    if case == "single-escape":
                        single = NativeContinuousSweepPlanFactory._build_native_plan(
                            native, factory._lease.source, sweep,
                            NativeContinuousSweepPlanFactory.preflight_profile(profile, sweep),
                            source_id="foreign-single")
                        refuses(lambda: coordinator.configure(single))
                    elif case == "foreign-pair":
                        foreign = native.PairedContinuousSweepCoordinatorConfig(
                            "different-resource", config.primary, config.secondary)
                        refuses(lambda: coordinator.configure_paired(foreign))
                    else:
                        coordinator.configure_paired(config)
                        if case == "running-stale":
                            coordinator.start()  # Direct adapter call owns the control transaction.
                            deadline = time.monotonic() + 5
                            while not coordinator.poll_paired_lines(1):
                                assert time.monotonic() < deadline
                                time.sleep(.001)
                        original = graph.sources._state
                        graph.sources._state = replace(original, revision=original.revision + 1)
                        try:
                            before_refusal = hooks.mock_iio_rf_mutation_calls()
                            refuses(coordinator.start)
                            # Running native tuner continues asynchronously; this
                            # refusal test is asserted strictly before any Start only.
                            if case == "stale-start":
                                assert hooks.mock_iio_rf_mutation_calls() == before_refusal == writes
                            refuses(coordinator.poll_paired_lines)
                            coordinator.stop()  # Stale selection cannot strand cleanup.
                            assert hooks.mock_iio_live_buffers() == 0
                            coordinator.disconnect()
                            factory = None
                            assert graph.live._pane_control_claim is None and not service._sweep_lease_active
                            assert hooks.mock_iio_live_contexts() == 0
                        finally:
                            graph.sources._state = original
                        factory = NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent)
                        refuses(coordinator.start)
                        coordinator.disconnect()  # Old release cannot retire the new lease.
                        factory.build_paired()
                        assert graph.live._pane_control_claim is not None and service._sweep_lease_active
                    if case != "running-stale":
                        assert hooks.mock_iio_rf_mutation_calls() == writes
                    return
                else:
                    assert case == "workflow"
                    for operation in (lambda: graph.live.select_device(source), graph.live.start,
                                      lambda: graph.live.apply_configuration(profile),
                                      lambda: service.arm_native_recording(RecordingOptions(output_path="unused")),
                                      lambda: factory.build(sweep), factory.create_display_service):
                        refuses(operation)
                    assert hooks.mock_iio_rf_mutation_calls() == writes
                    coordinator = factory.create_coordinator()
                    assert hooks.mock_iio_created_contexts() == contexts + 1
                    refuses(factory.create_coordinator)
                    with factory.control_transaction():
                        coordinator.configure_paired(config)
                        assert hooks.mock_iio_rf_mutation_calls() == writes
                        assert hooks.mock_iio_live_buffers() == 0
                        coordinator.start()
                    deadline = time.monotonic() + 5
                    lines = []
                    while not lines and time.monotonic() < deadline:
                        lines = coordinator.poll_paired_lines(2)
                        time.sleep(.001)
                    assert lines and len(lines) <= 2
                    for line in lines:
                        assert line.resource_id == intent.resource_id
                        assert line.primary.source.source_id == "product-left"
                        assert line.secondary.source.source_id == "product-right"
                        assert line.primary.epoch == line.secondary.epoch >= sweep.epoch
                        assert len(line.steps) == 3
                        assert line.primary.statistics is not None and line.secondary.statistics is not None
                    with factory.control_transaction():
                        coordinator.stop()
                    factory.close()
                    factory = None
                    assert graph.live._pane_control_claim is None
                    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                    # Explicit re-observation is allowed again, with fresh actual selection.
                    graph.live.select_device(source)
                    return
        assert hooks.mock_iio_rf_mutation_calls() == writes
        assert hooks.mock_iio_created_contexts() == contexts
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        if factory is None:
            assert graph.live._pane_control_claim is None and not service._sweep_lease_active
    finally:
        if factory is not None:
            factory.close()
        service.close_live()
        catalog.close()
    assert graph.live._pane_control_claim is None
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    print(f"paired product lease {case} PASS; compiled mock ONLY")


@unittest.skipUnless(MODULE and MOCK and Path(MOCK).is_file(), "requires explicit matching native/mock")
class PairedSweepProductLeaseTests(unittest.TestCase):
    def run_native(self, case):
        environment = dict(os.environ, LIBIIO_DLL_PATH=str(Path(MOCK).resolve(strict=True)),
            SDR_MOCK_LIBIIO_TOPOLOGY_DUAL="1",
            SDR_MOCK_LIBIIO_REFILL_DELAY_MS="1")
        if case == "single":
            environment.pop("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", None)
        if case == "empty-serial":
            environment["SDR_MOCK_LIBIIO_EMPTY_SERIAL"] = "1"
        result = subprocess.run([sys.executable, "-c",
            "from tests.test_app07_paired_sweep_product_lease import run_case; import sys; run_case(sys.argv[1],sys.argv[2])",
            MODULE, case], cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_application_pair_plan_start_stop_release(self): self.run_native("workflow")
    def test_actual_reselection_invalidates_staged_request(self): self.run_native("stale-revision")
    def test_selection_change_after_lease_refuses_before_open(self): self.run_native("changed-after-lease")
    def test_single_rx_refuses(self): self.run_native("single")
    def test_empty_serial_refuses(self): self.run_native("empty-serial")
    def test_armed_recording_refuses(self): self.run_native("armed")
    def test_existing_paired_rtbw_refuses(self): self.run_native("rtbw")
    def test_old_native_protocol_refuses_before_open(self): self.run_native("protocol")
    def test_aggregate_budget_refuses_before_open(self): self.run_native("budget")
    def test_failed_cleanup_retains_native_and_application_authorities(self): self.run_native("cleanup")
    def test_paired_owner_cannot_configure_a_single_plan(self): self.run_native("single-escape")
    def test_paired_owner_cannot_replace_the_admitted_pair(self): self.run_native("foreign-pair")
    def test_direct_start_rechecks_application_selection(self): self.run_native("stale-start")
    def test_running_stale_selection_cleanup_and_old_adapter_aba(self): self.run_native("running-stale")
    def test_released_claim_cannot_be_recreated_by_paused_start(self): self.run_native("race-control")
    def test_released_claim_cannot_be_recreated_by_paused_cleanup(self): self.run_native("race-cleanup")
