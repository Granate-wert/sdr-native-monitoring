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
            if case in ("budget", "domain-budget", "statistics-budget"):
                assert "aggregate 128MiB" in str(error), error
            return
        raise AssertionError("paired admission must refuse before owning/opening")

    try:
        source = graph.live.discover(startup=True)[0].device_id
        graph.live.select_device(source)
        profile = LiveConfiguration(center_hz=2450e6, sample_rate_hz=61_440_000.,
            analog_bandwidth_hz=56e6, fft_size=8192 if case == "domain-budget" else 4096,
            gain_db=20., averaging_frames=1,
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

        if case == "placeholder-serial":
            # Unknown observations must not downgrade paired admission to the
            # legacy unbound single-route constructor, even when nonblank.
            selected = replace(selected, device=replace(device, serial="unknown"))
            device = selected.device
            service._snapshot = selected
            refuses_admission(request())
        elif case in ("single", "empty-serial"):
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
            elif case == "reservation-protocol":
                with patch.object(native, "PLUTO_PAIRED_SWEEP_PRODUCT_RESERVATION_PROTOCOL_VERSION", 0):
                    refuses(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent))
            elif case in ("domain-budget", "statistics-budget"):
                from sdr_monitor.services.native_sweep import NativeSweepSource

                wide = (replace(intent, sweep=replace(sweep, start_hz=70e6, stop_hz=2230e6,
                                                      statistics=None, output_queue_capacity=1))
                        if case == "domain-budget" else replace(intent, sweep=replace(sweep,
                            statistics=replace(stats, power_bins=256, density_columns=2048))))
                preflight = NativeContinuousSweepPlanFactory.preflight_profile(profile, wide.sweep)
                source_value = NativeSweepSource(service._native_uri, "budget-evidence", profile,
                                                  expected_serial=device.serial)
                plans = [NativeContinuousSweepPlanFactory._build_native_plan(native, source_value,
                    wide.sweep, preflight, source_id=producer, receiver_selection=rx)
                    for producer, rx in ((intent.pair.primary_source_id, native.PlutoReceiverSelection.RX1),
                                         (intent.pair.secondary_source_id, native.PlutoReceiverSelection.RX2))]
                slots = 2 * wide.sweep.output_queue_capacity + 5
                old_reserved = preflight.segment_count * (slots + 1) * 4096
                old_reserved += preflight.reduced.output_bins * (
                    4 * slots + 2 if case == "domain-budget" else 36 * slots + 64) + (slots + 1) * 4096
                # Earlier undercount fits: native itself is not the cause of refusal.
                native.PairedContinuousSweepCoordinatorConfig(intent.resource_id, *plans,
                    product_publication_reserved_bytes=old_reserved)
                refuses_admission(wide)
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
                assert config.product_publication_reserved_bytes > 0
                assert hooks.mock_iio_rf_mutation_calls() == writes
                assert hooks.mock_iio_created_contexts() == contexts
                if case == "reservation":
                    # Both ordinary native plans fit, but downstream metadata
                    # consumes the SAME reduced/native whole-owner allowance.
                    native.PairedContinuousSweepCoordinatorConfig(intent.resource_id, config.primary, config.secondary)
                    for reservation in (128 * 1024 * 1024, (1 << 64) - 1):
                        refuses(lambda: native.PairedContinuousSweepCoordinatorConfig(
                            intent.resource_id, config.primary, config.secondary,
                            product_publication_reserved_bytes=reservation))
                    assert hooks.mock_iio_rf_mutation_calls() == writes
                    assert hooks.mock_iio_created_contexts() == contexts
                    return
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
                elif case.startswith("run-"):
                    from sdr_monitor.domain.identity import TimestampQuality
                    from sdr_monitor.services.native_paired_sweep_receipts import observed_paired_publication

                    coordinator = factory.create_coordinator()
                    coordinator.configure_paired(config)
                    assert coordinator.active_run is None
                    assert graph.live._paired_sweep_acquisition_epoch == 0
                    if case == "run-overflow":
                        graph.live._paired_sweep_acquisition_epoch = (1 << 64) - 1
                        refuses(coordinator.start)
                        assert coordinator.active_run is None
                        assert hooks.mock_iio_rf_mutation_calls() == writes
                        return
                    if case == "run-start-failure":
                        raw = coordinator._owner

                        class FailedStart:
                            def state(self): return raw.state()
                            def start(self): raise RuntimeError("injected Start failure")

                        coordinator._owner = FailedStart()
                        try:
                            refuses(coordinator.start)
                            assert coordinator.active_run is None
                            assert graph.live._paired_sweep_acquisition_epoch == 1
                            assert hooks.mock_iio_rf_mutation_calls() == writes
                        finally:
                            coordinator._owner = raw
                    run = coordinator.start()
                    assert run.request is intent and run.start_snapshot.applied.applied == profile
                    assert run.acquisition_epoch == (2 if case == "run-start-failure" else 1)
                    assert coordinator.active_run is run
                    refuses(lambda: coordinator.poll_observed_lines(sweep.output_queue_capacity + 1))
                    refused_epoch = graph.live._paired_sweep_acquisition_epoch
                    refuses(coordinator.start)
                    assert coordinator.active_run is run
                    assert graph.live._paired_sweep_acquisition_epoch == refused_epoch
                    deadline = time.monotonic() + 5
                    observed = []
                    progress = None
                    while not observed and time.monotonic() < deadline:
                        value = coordinator.poll_observed_progress()
                        if value is not None:
                            progress = value
                        observed = coordinator.poll_observed_lines(2)
                        time.sleep(.001)
                    assert observed
                    publication = observed[0]
                    assert publication.run is run and len(publication.steps) == 3
                    for item in (*observed, *((progress,) if progress is not None else ())):
                        for step in item.steps:
                            assert step.primary.identity.acquisition_epoch == run.acquisition_epoch
                            assert step.primary.identity.profile == profile
                            assert step.primary.identity.selection_revision == selection.revision
                            assert step.primary.timestamp_quality is TimestampQuality.UNKNOWN
                            assert step.primary.clock_domain is None
                            assert step.primary.quality_flags & 8192
                            step.validate_active(intent, step.primary.identity)
                    if case == "run-progress":
                        assert progress is not None, "no progressive observed publication before terminal"
                        assert 1 <= len(progress.steps) < 3
                    if case == "run-bundle-bool":
                        from sdr_monitor.domain.analyzer import bundles_from_paired_sweep

                        bundle = bundles_from_paired_sweep(publication)[0][1]
                        refuses(lambda: replace(bundle, acquisition_epoch=True))
                    if case == "run-bundle-instrument":
                        from sdr_monitor.domain.analyzer import bundles_from_paired_sweep
                        from sdr_monitor.domain.paired_sweep_publication import PairedSweepPublication
                        from tests.test_app07_mixed_source_trace import trace_bundle

                        primary = trace_bundle("product-left", publication.primary.epoch).spectrum
                        secondary = replace(primary, source_id="product-right")
                        # Real typed instrument frames, not a bypass of their
                        # validation. They are not AD paired RF provenance.
                        forged = PairedSweepPublication(run, (), primary, secondary)
                        refuses(lambda: bundles_from_paired_sweep(forged))
                    if case == "run-bundles":
                        from sdr_monitor.domain.analyzer import bundles_from_paired_sweep

                        assert progress is not None
                        for item in (publication, progress):
                            bundles = bundles_from_paired_sweep(item)
                            assert tuple(key for key, _ in bundles) == ("product-left", "product-right")
                            for (producer, bundle), receiver, frame in zip(bundles, ("RX1", "RX2"),
                                    (item.primary, item.secondary), strict=True):
                                assert bundle.spectrum is frame and bundle.paired_sweep is item
                                assert bundle.frequencies_hz is frame.frequencies_hz
                                assert bundle.identity.source_id == producer
                                assert bundle.identity.session_id == run.request.pair.session_id
                                assert bundle.identity.receiver_id == receiver
                                assert bundle.identity.acquisition_epoch == run.acquisition_epoch
                                assert frame.epoch != run.acquisition_epoch
                                assert bundle.identity.clock_domain is None
                                assert bundle.identity.config_generation is None
                                assert bundle.rtbw is None and bundle.mode == "sweep"
                                assert bundle.identity.accumulation_id == (
                                    f"paired-attempt:{run.acquisition_epoch}:epoch:{frame.epoch}")
                                for mutation in ({"receiver_id": "RX2" if receiver == "RX1" else "RX1"},
                                        {"session_id": "foreign"}, {"acquisition_epoch": frame.epoch},
                                        {"paired_sweep": None}, {"spectrum": replace(frame)}):
                                    refuses(lambda: replace(bundle, **mutation))
                    # Capture a genuine immutable native packet for adversarial
                    # conversion tests and stale replay across a new run.
                    deadline = time.monotonic() + 5
                    packets = []
                    while not packets and time.monotonic() < deadline:
                        packets = coordinator.poll_paired_lines(1)
                        time.sleep(.001)
                    assert packets
                    packet = packets[0]
                    if case == "run-forged":
                        def copied(value, **changes):
                            fields = {name: getattr(value, name) for name in dir(value)
                                      if not name.startswith('_') and not callable(getattr(value, name))}
                            fields.update(changes)
                            return SimpleNamespace(**fields)

                        primary = packet.primary
                        bad_source = copied(primary.source, device_serial="different")
                        bad_chain = copied(primary.source, metadata_json={"receiver_selection": '"RX2"'})
                        mutations = [copied(packet, resource_id="foreign"),
                            copied(packet, primary=copied(primary, epoch=primary.epoch + 1)),
                            copied(packet, primary=copied(primary, source=bad_source)),
                            copied(packet, primary=copied(primary, source=bad_chain)),
                            copied(packet, steps=()),
                            copied(packet, steps=(copied(packet.steps[0], config_generation=99999), *packet.steps[1:])),
                            copied(packet, steps=(copied(packet.steps[0], center_frequency_hz=1e6), *packet.steps[1:]))]
                        for mutation in mutations:
                            refuses(lambda: observed_paired_publication(mutation, run))
                        assert coordinator.active_run is run
                    coordinator.request_stop()
                    assert coordinator.active_run is None
                    refuses(coordinator.poll_observed_lines)
                    refuses(coordinator.poll_observed_progress)
                    coordinator.stop()
                    coordinator.configure_paired(config)
                    assert coordinator.active_run is None
                    new_run = coordinator.start()
                    assert new_run.acquisition_epoch == run.acquisition_epoch + 1
                    assert new_run is not run
                    refuses(lambda: coordinator._observe(packet))
                    deadline = time.monotonic() + 5
                    fresh = []
                    while not fresh and time.monotonic() < deadline:
                        fresh = coordinator.poll_observed_lines(1)
                        time.sleep(.001)
                    assert fresh and fresh[0].run is new_run
                    assert fresh[0].primary.epoch > publication.primary.epoch
                    if case == "run-bundles":
                        old = bundles_from_paired_sweep(publication)
                        new = bundles_from_paired_sweep(fresh[0])
                        assert old[0][1].identity.acquisition_epoch != new[0][1].identity.acquisition_epoch
                        assert old[0][1].identity.accumulation_id != new[0][1].identity.accumulation_id
                    coordinator.stop()
                    coordinator.disconnect()
                    assert coordinator.active_run is None
                    factory = None
                    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                    assert graph.live._pane_control_claim is None and not service._sweep_lease_active
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
    def test_owner_issued_run_and_observed_steps_rearm_stale_replay(self): self.run_native("run-workflow")
    def test_progressive_observed_pair_precedes_terminal(self): self.run_native("run-progress")
    def test_foreign_receipts_serial_chain_generation_geometry_refuse(self): self.run_native("run-forged")
    def test_failed_start_consumes_epoch_without_admitting_publication(self): self.run_native("run-start-failure")
    def test_run_epoch_exhaustion_refuses_before_rf(self): self.run_native("run-overflow")
    def test_nonblank_placeholder_serial_refuses_before_paired_lease(self): self.run_native("placeholder-serial")
    def test_old_product_reservation_protocol_refuses_before_open(self): self.run_native("reservation-protocol")
    def test_product_reservation_counts_in_native_budget_before_open(self): self.run_native("reservation")
    def test_full_converted_array_budget_refuses_near_boundary_before_open(self): self.run_native("domain-budget")
    def test_density_cell_validation_budget_refuses_before_open(self): self.run_native("statistics-budget")
    def test_actual_observed_pair_uses_common_analyzer_envelope_without_epoch_rewrite(self): self.run_native("run-bundles")
    def test_paired_analyzer_epoch_refuses_boolean(self): self.run_native("run-bundle-bool")
    def test_paired_analyzer_refuses_borrowed_instrument_provenance(self): self.run_native("run-bundle-instrument")
