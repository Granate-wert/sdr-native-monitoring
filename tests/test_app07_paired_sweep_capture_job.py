"""Actual NativeLive/factory/coordinator -> common CaptureJob; compiled mock ONLY."""

from dataclasses import replace
import time
from unittest.mock import patch

from sdr_monitor.domain.analyzer import bundles_from_paired_sweep
from sdr_monitor.domain.pane_scheduler import Ad936xPairedSweepPaneProfile, CaptureEpochCost, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverChainSelection, ReceiverEndpoint
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.ad936x_paired_sweep_pane_owner import Ad936xPairedSweepPaneOwner
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from tests.test_app07_shared_capture_schedule import pane


def run_pane_case(graph, intent, case, hooks):
    selection = graph.live.current_source_selection()
    profile = Ad936xPairedSweepPaneProfile(selection.selected, selection.revision,
        intent.pair.configuration, intent.sweep, CaptureEpochCost(.01, .01, .05, .005, .005),
        paired_request=intent)
    endpoints = tuple(ReceiverEndpoint(producer, intent.pair.device_id, intent.resource_id, chain)
        for producer, chain in ((intent.pair.primary_source_id, ReceiverChainSelection.RX1),
                                (intent.pair.secondary_source_id, ReceiverChainSelection.RX2)))
    group = AcquisitionGroup("paired-group", intent.resource_id, endpoints)
    # Disjoint crops still share the full exact tuner plan, not two cropped LO plans.
    left_range = (2434.5e6, 2435.5e6) if case == "pane-overlap" else (2400e6, 2410e6)
    requests = (pane("left", endpoints[0].endpoint_id, *left_range),
                pane("right", endpoints[1].endpoint_id, 2470e6, 2479e6))
    schedule = compile_pane_schedule((group,), requests, {"capture": profile})
    job = schedule.resources[0].jobs[0]
    assert (job.start_hz, job.stop_hz) == (intent.sweep.start_hz, intent.sweep.stop_hz)
    def make_owner():
        return Ad936xPairedSweepPaneOwner(graph.live, physical_stream_resource_id=intent.resource_id,
            source_id=intent.pair.device_id, endpoints=endpoints)
    owner = make_owner()
    clock = [100.0]
    session = PaneResourceSession(schedule, (group,), {intent.resource_id: owner}, ReceiverLeaseManager(),
        owner_factories={intent.resource_id: make_owner}, now_s=lambda: clock[0])
    created, writes = hooks.mock_iio_created_contexts(), hooks.mock_iio_rf_mutation_calls()
    assert graph.live._pane_control_claim is None
    assert hooks.mock_iio_created_contexts() == created and hooks.mock_iio_rf_mutation_calls() == writes

    def refused(operation):
        try:
            operation()
        except (ValueError, RuntimeError, TypeError):
            return
        raise AssertionError("operation did not refuse")

    try:
        session.apply()
        if case == "pane-profile":
            refused(lambda: replace(profile, selection_revision=selection.revision + 1))
            refused(lambda: replace(job, receiver_endpoint_ids=(endpoints[0].endpoint_id,)))
            refused(lambda: replace(job, stop_hz=2479e6))
            refused(lambda: replace(job, crops=(job.crops[0], replace(job.crops[1],
                                        receiver_endpoint_id=endpoints[0].endpoint_id))))
            return
        activation = session.start_resource(intent.resource_id)
        assert graph.live._pane_control_claim is owner._control_claim
        assert hooks.mock_iio_created_contexts() == created + 1
        coordinator = owner._coordinator
        run = coordinator.active_run
        assert run is not None
        if case == "pane-claim":
            # A captured token outside the owner transaction cannot borrow admission.
            refused(lambda: NativeContinuousSweepPlanFactory.from_paired_application(
                graph.live, intent, control_claim=owner._control_claim))
            refused(lambda: graph.live.pane_control_transaction(object()).__enter__())
            refused(lambda: graph.live.apply_configuration(intent.pair.configuration))
            refused(lambda: NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent))
        if case == "pane-cleanup":
            original = owner._factory.close
            def fail():
                raise RuntimeError("injected factory cleanup failure")
            with patch.object(owner._factory, "close", fail):
                refused(lambda: session.stop_resource(intent.resource_id))
            assert graph.live._pane_control_claim is owner._control_claim
            assert owner._factory is not None and owner._factory.close == original
            refused(lambda: session.start_resource(intent.resource_id))
            session.stop_resource(intent.resource_id)
            assert graph.live._pane_control_claim is None
            return
        progress = None
        lines = ()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = coordinator.poll_observed_progress()
            if value is not None and progress is None:
                progress = value
            lines = coordinator.poll_observed_lines()
            if lines and progress is not None:
                break
            time.sleep(.001)
        assert lines and progress is not None, "need observed prefix and terminal"
        pair = lines[0]
        if case == "pane-index":
            for bad_index in (100, -2, 0):
                indices = pair.primary.source_segment_indices.copy()
                indices[pair.primary.frequencies_hz >= 2470e6] = bad_index
                primary = replace(pair.primary, source_segment_indices=indices)
                secondary = replace(pair.secondary, source_segment_indices=indices)
                try:
                    forged = replace(pair, primary=primary, secondary=secondary)
                except ValueError:
                    pass  # preferred domain refusal before any delivery
                else:
                    assert session.accept_paired_sweep(activation, bundles_from_paired_sweep(forged)) == ()
            assert session.pane_age_s("left") is session.pane_age_s("right") is None
            assert "left" not in session._pane_last_received and "right" not in session._pane_last_received
            return
        if case == "pane-atomic":
            bundles = bundles_from_paired_sweep(progress)
            assert session.accept_frame(activation, *bundles[0]) == ()
            assert session.accept_paired_sweep(activation, bundles[:1]) == ()
            assert session.accept_paired_sweep(activation, (bundles[0], bundles[0])) == ()
            clone = bundles_from_paired_sweep(replace(progress, run=replace(run)))
            assert session.accept_paired_sweep(activation, clone) == ()
            assert session.accept_paired_sweep(activation, (bundles[0], clone[1])) == ()
            assert session.pane_age_s("left") is session.pane_age_s("right") is None
            # First is valid, second fails exact endpoint identity. No first-half commit.
            assert session.accept_paired_sweep(activation, (bundles[0], (bundles[1][0], bundles[0][1]))) == ()
            assert session.pane_age_s("left") is session.pane_age_s("right") is None
            assert len(session.accept_paired_sweep(activation, bundles)) == 2
            assert session.accept_paired_sweep(activation, bundles) == ()
        elif case in ("pane-pending", "pane-overlap"):
            assert isinstance(progress.primary, SweepProgressFrame)
            assert len(session.accept_paired_sweep(activation, bundles_from_paired_sweep(progress))) == 2
            assert session.pane_age_s("left") is not None
            assert session.pane_age_s("right") is None  # wholly pending crop is not a measured visit
        else:
            with (patch.object(coordinator, "poll_observed_lines", return_value=(pair,)),
                  patch.object(coordinator, "poll_observed_progress", return_value=None)):
                deliveries = session.poll_resource(intent.resource_id)
            assert len(deliveries) == 2
            assert deliveries[0].bundle.spectrum is pair.primary
            assert deliveries[1].bundle.spectrum is pair.secondary
            assert deliveries[0].host_received_monotonic_s == deliveries[1].host_received_monotonic_s
        terminal = bundles_from_paired_sweep(pair)
        # Terminal after progress still commits both independently cropped views.
        if case in ("pane-atomic", "pane-pending", "pane-overlap"):
            clock[0] = 110.0
            assert len(session.accept_paired_sweep(activation, terminal)) == 2
            if case == "pane-pending":
                assert pair.primary.sequence == progress.primary.sequence
                assert session.pane_age_s("left") == 10.0, "cumulative old first-segment bins falsely refreshed"
                assert session.pane_age_s("right") == 0.0
            if case == "pane-overlap":
                grid = pair.primary.frequencies_hz
                overlap = (grid >= left_range[0]) & (grid <= left_range[1])
                assert len(progress.steps) == 1
                assert bool((pair.primary.source_segment_indices[overlap] == 0).all())
                assert session.pane_age_s("left") == 0.0, "new overlap contribution kept first owner but failed freshness"
        session.stop_resource(intent.resource_id)
        assert graph.live._pane_control_claim is None
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert session.accept_paired_sweep(activation, terminal) == ()
        session.rearm_resource(intent.resource_id)
        next_activation = session.start_resource(intent.resource_id)
        owner = session._runtimes[intent.resource_id].owner
        assert owner._coordinator.active_run.acquisition_epoch > run.acquisition_epoch
        assert session.accept_paired_sweep(next_activation, terminal) == ()
        deadline = time.monotonic() + 5
        delivered = ()
        while not delivered and time.monotonic() < deadline:
            delivered = session.poll_resource(intent.resource_id)
            time.sleep(.001)
        assert len(delivered) in (2, 4)
        assert all(item.bundle.acquisition_epoch > run.acquisition_epoch for item in delivered)
    finally:
        session.stop_resource(intent.resource_id)
    assert graph.live._pane_control_claim is None
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
