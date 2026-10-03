"""Actual compiled MOCK-IIO user Stage/Apply/Start over the same V2 owner."""

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest

from tests.test_app07_native_paired_live import MOCK, MODULE, ROOT


def run_case(path: str, case: str) -> None:
    import ctypes
    from unittest.mock import patch

    from sdr_monitor.domain.identity import SessionId
    from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
    from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
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
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
                                      control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, device_catalog=catalog, analyzer_display=_UnusedSweep()))
    prepared = None
    try:
        source = graph.live.discover(startup=True)[0].device_id
        drafts = (PaneSlotDraft(1, source, 2_440e6, 2_450e6, sample_rate_hz=61_440_000.),
                  PaneSlotDraft(2, source, 2_450e6, 2_460e6, sample_rate_hz=61_440_000.,
                                receiver_selection=ReceiverChainSelection.RX2),
                  PaneSlotDraft(3), PaneSlotDraft(4))
        writes = hooks.mock_iio_rf_mutation_calls()
        factory_calls = []

        def factory(resource):
            factory_calls.append(resource)
            return graph

        if case in {"single-layout", "empty-serial"}:
            try:
                prepare_user_pane_session(drafts, pool_factory=lambda: PaneProductGraphPool(factory))
            except PaneUserStageError as error:
                assert error.pool is None  # Failed Stage confirmed cleanup.
                assert error.reason is (PaneUserRefusal.PAIRED_TOPOLOGY_UNAVAILABLE if case == "single-layout"
                                        else PaneUserRefusal.PAIRED_STABLE_IDENTITY_REQUIRED)
            else:
                raise AssertionError("unqualified current selected pair must refuse")
            assert hooks.mock_iio_rf_mutation_calls() == writes
            return
        prepared = prepare_user_pane_session(drafts, pool_factory=lambda: PaneProductGraphPool(factory))
        handle = prepared.handle
        resource = "pane-resource-1"
        assert factory_calls == [resource]
        assert len(prepared.preview) == len(prepared.plan.groups) == 1
        assert len(prepared.plan.layout.schedule.resources[0].jobs) == 1
        assert handle.session.retained_resource_count == 0
        assert not handle.applied and not graph.live.is_running()
        assert graph.live.current_snapshot().applied is None
        assert hooks.mock_iio_rf_mutation_calls() == writes
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        if case == "stale-apply":
            current = graph.live.current_snapshot()
            with patch.object(graph.live, "current_snapshot", return_value=replace(
                    current, session_id=SessionId("stale-session"))):
                try:
                    apply_user_pane_session(prepared)
                except PaneUserStageError as error:
                    assert error.reason is PaneUserRefusal.SELECTION_CHANGED
                else:
                    raise AssertionError("stale paired Apply must refuse")
            assert not handle.applied and not handle.pump.activated
            assert handle.session.retained_resource_count == 0
            assert graph.live.current_snapshot().applied is None
            assert hooks.mock_iio_rf_mutation_calls() == writes
            return
        apply_user_pane_session(prepared)
        assert handle.applied and not graph.live.is_running()
        assert graph.live.current_snapshot().applied.applied == prepared.plan.initial_ad_configurations[0][1]
        assert handle.session.retained_resource_count == 1
        assert hooks.mock_iio_rf_mutation_calls() == writes
        activation = handle.pump.start_resource(resource).result(timeout=5.)
        assert graph.live.is_running()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1

        def receive_pair():
            delivered = {}
            deadline = time.monotonic() + 5.
            while time.monotonic() < deadline:
                for item in handle.queue.drain():
                    delivered[item.binding.receiver_selection] = item
                if set(delivered) == {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}:
                    return delivered
                time.sleep(.005)
            raise AssertionError("both actual reduced producers must reach the user-plan queue")

        first = receive_pair()
        assert len({item.bundle.acquisition_epoch for item in first.values()}) == 1
        assert all(item.delivery.host_activation_serial == activation.host_activation_serial
                   and item.bundle.receiver_id == chain.name
                   and item.producer_source_id == item.binding.receiver_endpoint_id
                   and item.bundle.spectrum.sample_rate_hz == 61_440_000.
                   and item.bundle.paired_capture is not None for chain, item in first.items())
        epoch = next(iter(first.values())).bundle.acquisition_epoch
        if case == "stale-rearm":
            handle.pump.stop_resource(resource).result(timeout=5.)
            graph.live.select_device(source)
            graph.live.apply_configuration(prepared.plan.initial_ad_configurations[0][1])
            writes = hooks.mock_iio_rf_mutation_calls()
            try:
                handle.pump.start_resource(resource).result(timeout=5.)
            except RuntimeError:
                pass
            else:
                raise AssertionError("rearm must not adopt a newly selected session without fresh Stage")
            assert hooks.mock_iio_rf_mutation_calls() == writes
            assert not graph.live.is_running()
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            return
        before = hooks.mock_iio_rf_mutation_calls()
        preview = handle.preview_rf_shift(1, 1_000_000.).result(timeout=5.)
        assert hooks.mock_iio_rf_mutation_calls() == before and graph.live.is_running()
        assert preview.proposal.proposed_context.drafts[1] == drafts[1]
        handle.stop_for_rf_shift(preview).result(timeout=5.)
        assert not graph.live.is_running()
        handle.apply_rf_shift(preview).result(timeout=5.)
        assert not graph.live.is_running()  # Apply never hides Start.
        restarted = handle.pump.start_resource(resource).result(timeout=5.)
        second = receive_pair()
        assert restarted.host_activation_serial > activation.host_activation_serial
        assert all(item.bundle.acquisition_epoch > epoch for item in second.values())
        assert len({item.bundle.spectrum.center_frequency_hz for item in second.values()}) == 1
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1
        print("actual native MOCK user Stage/Apply/separateStart/common RF shift/both producers PASS")
    finally:
        if prepared is not None:
            if prepared.handle.pump.activated:
                for future in prepared.handle.pump.stop_all().values():
                    future.result(timeout=5.)
            prepared.handle.shutdown_after_stop()
            assert prepared.handle.session.retained_resource_count == 0
        graph.live.shutdown()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert service._poller is service._engine is None


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit native module and built Mock IIO")
class NativePairedUserStageTests(unittest.TestCase):
    def run_case(self, case):
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        if case == "single-layout":
            environment.pop("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", None)
        if case == "empty-serial":
            environment["SDR_MOCK_LIBIIO_EMPTY_SERIAL"] = "1"
        else:
            environment.pop("SDR_MOCK_LIBIIO_EMPTY_SERIAL", None)
        result = subprocess.run([sys.executable, "-m", __name__, MODULE, case], cwd=ROOT,
                                env=environment, capture_output=True, text=True, timeout=30.)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fresh_user_stage_apply_separate_start_and_common_rf_shift(self):
        self.run_case("delivery")

    def test_changed_session_refuses_before_apply_configuration_or_lease(self):
        self.run_case("stale-apply")

    def test_rearm_does_not_adopt_new_selected_session_without_fresh_stage(self):
        self.run_case("stale-rearm")

    def test_missing_dual_layout_refuses_before_apply_or_rf(self):
        self.run_case("single-layout")

    def test_empty_serial_route_cannot_borrow_paired_stable_admission(self):
        self.run_case("empty-serial")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        run_case(sys.argv[1], sys.argv[2])
    else:
        unittest.main()
