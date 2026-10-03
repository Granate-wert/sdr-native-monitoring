"""Actual V2 user compiler -> one compiled MOCK-IIO paired Sweep owner.

The phase gate is test-DLL only. No RF, physical dual-RX, paint or rate proof.
"""

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest

from tests.native_test_dependencies import explicit_native_dependencies
from tests.test_app07_paired_sweep_binding import MODULE, MOCK, ROOT


@explicit_native_dependencies
def run_case(path: str, case: str) -> None:
    import ctypes
    from unittest.mock import patch

    from sdr_monitor.domain.continuous_sweep_geometry import sweep_step_geometry
    from sdr_monitor.domain.identity import SessionId
    from sdr_monitor.domain.pane_scheduler import Ad936xPairedSweepPaneProfile, CaptureMeasurementMode
    from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
    from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
    from sdr_monitor.domain.sweep_progress import SweepProgressFrame
    from sdr_monitor.services.ad936x_paired_sweep_pane_owner import Ad936xPairedSweepPaneOwner
    from sdr_monitor.services.native_live import NativeLiveSessionService
    from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
    from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
    from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
    from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
    from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
    from sdr_monitor.ui.v2_pane_user_stage import (
        PaneUserStageError, apply_user_pane_session, prepare_user_pane_session,
    )
    from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep

    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    assert spec is not None and spec.loader is not None
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == Path(path).resolve()
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    hooks.mock_iio_set_phase_gate.argtypes = [ctypes.c_int, ctypes.c_longlong, ctypes.c_int]
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
        control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, device_catalog=catalog, analyzer_display=_UnusedSweep()))
    prepared = None
    try:
        source = graph.live.discover(startup=True)[0].device_id
        drafts = (
            PaneSlotDraft(1, source, 2400e6 + 123., 2410e6 + 321.,
                sample_rate_hz=61_440_000., fft_size=1024, measurement_mode=CaptureMeasurementMode.SWEEP),
            PaneSlotDraft(2, source, 2470e6 + 456., 2480e6 + 789.,
                sample_rate_hz=61_440_000., fft_size=1024, measurement_mode=CaptureMeasurementMode.SWEEP,
                receiver_selection=ReceiverChainSelection.RX2),
            PaneSlotDraft(3), PaneSlotDraft(4))
        writes = hooks.mock_iio_rf_mutation_calls()
        calls = []

        def factory(resource):
            calls.append(resource)
            return graph

        prepared = prepare_user_pane_session(drafts, pool_factory=lambda: PaneProductGraphPool(factory))
        handle = prepared.handle
        resource = "pane-resource-1"
        assert calls == [resource] and len(prepared.plan.groups) == len(prepared.preview) == 1
        assert not handle.applied and graph.live.current_snapshot().applied is None
        assert handle.session.retained_resource_count == 0
        assert hooks.mock_iio_rf_mutation_calls() == writes
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        schedule = prepared.plan.layout.schedule
        assert len(schedule.resources) == len(schedule.resources[0].jobs) == 1
        job = schedule.resources[0].jobs[0]
        assert isinstance(job.profile, Ad936xPairedSweepPaneProfile)
        assert (job.start_hz, job.stop_hz) == (drafts[0].start_hz, drafts[1].stop_hz)
        assert len(job.crops) == 2
        if case == "stale-apply":
            current = graph.live.current_snapshot()
            with patch.object(graph.live, "current_snapshot", return_value=replace(
                    current, session_id=SessionId("foreign-sweep-session"))):
                try:
                    apply_user_pane_session(prepared)
                except PaneUserStageError as error:
                    assert error.reason is PaneUserRefusal.SELECTION_CHANGED
                else:
                    raise AssertionError("stale paired Sweep Stage must refuse before Apply")
            assert not handle.applied and graph.live.current_snapshot().applied is None
            assert hooks.mock_iio_rf_mutation_calls() == writes
            return
        apply_user_pane_session(prepared)
        assert handle.applied and handle.session.retained_resource_count == 1
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert hooks.mock_iio_rf_mutation_calls() == writes
        contexts = hooks.mock_iio_created_contexts()
        geometry = sweep_step_geometry(job.profile.request_template, 1)
        hooks.mock_iio_set_phase_gate(1, int(geometry.center_hz), 1)
        activation = handle.pump.start_resource(resource).result(timeout=5.)
        owner = handle.session._runtimes[resource].owner
        assert isinstance(owner, Ad936xPairedSweepPaneOwner)
        assert hooks.mock_iio_created_contexts() == contexts + 1
        # Start admission precedes the acquisition worker's buffer creation;
        # tuning boundaries can retire a buffer while retaining ONE context.
        assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_live_buffers() <= 1
        deadline = time.monotonic() + 5.
        while not hooks.mock_iio_phase_gate_entered() and time.monotonic() < deadline:
            time.sleep(.001)
        assert hooks.mock_iio_phase_gate_entered() and not hooks.mock_iio_phase_gate_expired()
        assert hooks.mock_iio_created_buffers() == 1  # ONE first-step buffer feeds BOTH chains.
        if case in {"rf-apply-profile", "rf-apply-failure"}:
            hooks.mock_iio_release_phase_gate()
            handle.pump.stop_resource(resource).result(timeout=5.)
            preview = handle.preview_rf_shift(1, 1_000_000.).result(timeout=5.)
            before = hooks.mock_iio_rf_mutation_calls()
            if case == "rf-apply-failure":
                previous = handle.rf_context
                # Native owner stays stopped. A non-confirming host stage
                # must not expose Start from partially committed routing.
                with patch.object(graph.live, "apply_configuration", return_value=graph.live.current_snapshot()):
                    try:
                        handle.apply_rf_shift(preview).result(timeout=5.)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("unconfirmed paired Sweep profile must fail RF Apply")
                assert handle.rf_context is previous
                try:
                    handle.pump.start_resource(resource).result(timeout=5.)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("failed routing/profile commit must bar Start")
                assert hooks.mock_iio_rf_mutation_calls() == before
                assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
                return
            handle.apply_rf_shift(preview).result(timeout=5.)
            shifted = handle.rf_context.plan.initial_ad_configurations[0][1]
            assert graph.live.current_snapshot().applied.applied == shifted, "stopped RF Apply omitted paired Sweep profile"
            assert hooks.mock_iio_rf_mutation_calls() == before
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            return

        def receive_pair(progress_only=False, terminal_only=False):
            delivered = {}
            deadline = time.monotonic() + 5.
            while time.monotonic() < deadline:
                for item in handle.queue.drain():
                    if ((not progress_only or isinstance(item.bundle.spectrum, SweepProgressFrame))
                            and (not terminal_only or item.bundle.terminal_sweep)):
                        delivered[item.binding.receiver_selection] = item
                if set(delivered) == {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}:
                    return delivered
                time.sleep(.005)
            raise AssertionError("BOTH actual Sweep producers did not reach user-plan presentation queue")

        first = receive_pair(progress_only=True)
        assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_live_buffers() <= 1
        assert all(isinstance(item.bundle.spectrum, SweepProgressFrame)
                   and item.bundle.spectrum.revision == 1 and not item.bundle.terminal_sweep
                   and item.producer_source_id == item.binding.receiver_endpoint_id
                   and item.delivery.host_activation_serial == activation.host_activation_serial
                   and item.bundle.receiver_id == chain.name for chain, item in first.items())
        assert len({item.bundle.acquisition_epoch for item in first.values()}) == 1
        assert handle.session.pane_age_s("pane-1") is not None
        assert handle.session.pane_age_s("pane-2") is None  # All pending is not fresh.
        old_bundles = tuple((item.binding.receiver_endpoint_id, item.bundle) for item in first.values())
        hooks.mock_iio_release_phase_gate()
        terminal = receive_pair(terminal_only=True)
        assert all(item.bundle.terminal_sweep for item in terminal.values())
        assert handle.session.pane_age_s("pane-1") is not None
        assert handle.session.pane_age_s("pane-2") is not None
        before = hooks.mock_iio_rf_mutation_calls()
        preview = handle.preview_rf_shift(1, 1_000_000.).result(timeout=5.)
        assert hooks.mock_iio_rf_mutation_calls() == before
        assert preview.proposal.proposed_context.drafts[1] == drafts[1]
        handle.stop_for_rf_shift(preview).result(timeout=5.)
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert handle.session.accept_paired_sweep(activation, old_bundles) == ()
        if case == "stale-start":
            graph.live.select_device(source)
            graph.live.apply_configuration(prepared.plan.initial_ad_configurations[0][1])
            before = hooks.mock_iio_rf_mutation_calls()
            try:
                handle.pump.start_resource(resource).result(timeout=5.)
            except RuntimeError:
                pass
            else:
                raise AssertionError("paired Sweep rearm cannot silently adopt a new selected session")
            assert hooks.mock_iio_rf_mutation_calls() == before
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            return
        before = hooks.mock_iio_rf_mutation_calls()
        handle.apply_rf_shift(preview).result(timeout=5.)
        assert hooks.mock_iio_rf_mutation_calls() == before  # Stopped Apply is host-only.
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        shifted = handle.rf_context.plan.initial_ad_configurations[0][1]
        assert graph.live.current_snapshot().applied.applied == shifted
        restarted = handle.pump.start_resource(resource).result(timeout=5.)
        second = receive_pair()
        assert restarted.host_activation_serial > activation.host_activation_serial
        assert all(item.bundle.acquisition_epoch > first[chain].bundle.acquisition_epoch
                   and item.delivery.host_activation_serial == restarted.host_activation_serial
                   and item.bundle.session_id == job.profile.paired_request.pair.session_id
                   for chain, item in second.items())
        assert handle.session.accept_paired_sweep(activation, old_bundles) == ()
        assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_live_buffers() <= 1
        print("compiled MOCK: actual paired user Sweep Stage/Apply/progressiveBOTH/RF-shift/rearm PASS")
    finally:
        hooks.mock_iio_release_phase_gate()
        if prepared is not None:
            if prepared.handle.pump.activated:
                for future in prepared.handle.pump.stop_all().values():
                    future.result(timeout=5.)
            prepared.handle.shutdown_after_stop()
            assert prepared.handle.session.retained_resource_count == 0
        graph.live.shutdown()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert service._engine is service._poller is None


@unittest.skipUnless(MODULE and MOCK, "requires explicit matching native and Mock IIO")
class NativePairedUserSweepStageTests(unittest.TestCase):
    def run_case(self, case):
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        result = subprocess.run([sys.executable, "-m", __name__, MODULE, case], cwd=ROOT,
            env=environment, capture_output=True, text=True, timeout=30.)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_user_sweep_common_owner_progressive_pair_and_rf_shift(self):
        self.run_case("delivery")

    def test_changed_session_refuses_before_sweep_apply_or_lease(self):
        self.run_case("stale-apply")

    def test_sweep_rearm_refuses_new_session_without_fresh_stage(self):
        self.run_case("stale-start")

    def test_stopped_rf_apply_stages_exact_paired_sweep_profile_without_rx(self):
        self.run_case("rf-apply-profile")

    def test_unconfirmed_rf_apply_bars_start_and_remains_explicitly_cleanable(self):
        self.run_case("rf-apply-failure")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        run_case(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
