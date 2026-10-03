"""Actual admitted coordinator terminal archival, compiled mock IIO ONLY."""

import ctypes
from dataclasses import replace
import time

import numpy as np

from sdr_monitor.domain.analyzer import bundles_from_paired_sweep
from sdr_monitor.domain.continuous_sweep_geometry import sweep_step_geometry
from sdr_monitor.domain.paired_sweep_archive import PairedSweepTerminalArchive, PairedSweepUnobservedTerminal
from sdr_monitor.domain.sweep_lines import SweepLineState
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory


def run_archive_case(graph, intent, case, hooks):
    def refused(operation):
        try:
            operation()
        except (ValueError, TypeError, RuntimeError):
            return
        raise AssertionError("operation did not refuse")

    def wait(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            assert time.monotonic() < deadline, "mock phase gate timeout"
            time.sleep(.001)

    hooks.mock_iio_set_phase_gate.argtypes = [ctypes.c_int, ctypes.c_longlong, ctypes.c_int]
    zero = case == "archive-zero"
    geometry = sweep_step_geometry(intent.sweep, 0 if zero else 1)
    center = int((geometry.usable_start_hz + geometry.usable_stop_hz) / 2)
    factory = NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent)
    coordinator = factory.create_coordinator()
    config = factory.build_paired()
    coordinator.configure_paired(config)
    try:
        refused(coordinator.poll_retired_archive)  # configured, never started
        hooks.mock_iio_set_phase_gate(3 if zero else 1, center, 1)
        run = coordinator.start()
        refused(coordinator.poll_retired_archive)
        refused(coordinator.join)  # running join without requestStop cannot deadlock
        wait(lambda: hooks.mock_iio_phase_gate_entered() != 0)
        assert hooks.mock_iio_phase_gate_expired() == 0
        if not zero:
            prefix = coordinator.poll_observed_progress()
            assert prefix is not None and len(prefix.steps) == 1
        coordinator.request_stop()
        assert coordinator.active_run is None
        refused(coordinator.poll_observed_lines)
        refused(coordinator.poll_observed_progress)
        refused(coordinator.poll_retired_archive)  # requestStop is not confirmed join
        hooks.mock_iio_release_phase_gate()
        coordinator.join()
        writes, contexts = hooks.mock_iio_rf_mutation_calls(), hooks.mock_iio_created_contexts()
        archive = coordinator.poll_retired_archive()
        assert isinstance(archive, PairedSweepTerminalArchive)
        assert archive.run is run and len(archive.terminals) == 1
        terminal = archive.terminals[0]
        assert terminal.primary.state is terminal.secondary.state is SweepLineState.GAP
        assert "cancellation" in terminal.primary.gap_reasons
        refused(lambda: bundles_from_paired_sweep(archive))
        refused(lambda: replace(archive, run=replace(run)))
        refused(lambda: replace(archive, terminals=(terminal, terminal)))
        if zero:
            assert isinstance(terminal, PairedSweepUnobservedTerminal)
            assert not terminal.primary.segment_acquisition and terminal.primary.last_admitted_segment is None
            refused(lambda: bundles_from_paired_sweep(terminal))
            values = terminal.primary.values_db.copy()
            values[0] = -100.
            # The generic line may refuse already; either boundary must reject.
            refused(lambda: replace(terminal, primary=replace(terminal.primary, values_db=values)))
        else:
            assert len(terminal.steps) == 1
            assert terminal.steps[0].primary.identity.acquisition_epoch == run.acquisition_epoch
            assert terminal.primary.segment_acquisition == prefix.primary.segment_acquisition
        assert coordinator.poll_retired_archive().terminals == ()  # no cached re-delivery
        assert hooks.mock_iio_rf_mutation_calls() == writes and hooks.mock_iio_created_contexts() == contexts
        saved = terminal.primary.values_db.copy()
        coordinator.configure_paired(config)
        refused(coordinator.poll_retired_archive)  # old run removed before reconfigure/start
        new_run = coordinator.start()
        refused(coordinator.poll_retired_archive)
        assert new_run.acquisition_epoch > run.acquisition_epoch
        coordinator.stop()
        newer = coordinator.poll_retired_archive()
        assert newer.run is new_run
        assert all(item.primary.epoch > terminal.primary.epoch for item in newer.terminals)
        factory.close()
        refused(coordinator.poll_retired_archive)
        np.testing.assert_array_equal(terminal.primary.values_db, saved)
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert graph.live._pane_control_claim is None
    finally:
        hooks.mock_iio_release_phase_gate()
        factory.close()


def run_pane_archive_case(session, owner, activation, intent, hooks, case, refused):
    """Invoke actual resource Stop; retired output never becomes pane input."""
    from unittest.mock import patch

    coordinator = owner._coordinator
    deadline = time.monotonic() + 5
    while hooks.mock_iio_phase_gate_entered() == 0:
        assert time.monotonic() < deadline, "pane archive phase gate timeout"
        time.sleep(.001)
    prefix = coordinator.poll_observed_progress()
    assert prefix is not None and len(prefix.steps) == 1
    run = prefix.run
    assert len(session.accept_paired_sweep(activation, bundles_from_paired_sweep(prefix))) == 2
    coordinator.request_stop()
    hooks.mock_iio_release_phase_gate()
    if case == "pane-archive-error":
        with patch.object(coordinator, "poll_retired_archive", side_effect=RuntimeError("injected archive failure")):
            refused(lambda: session.stop_resource(intent.resource_id))
        assert owner._factory is None and owner.terminal_archive is None
        assert "injected archive failure" in owner.terminal_archive_error
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert owner._live._pane_control_claim is owner._control_claim
        refused(lambda: session.start_resource(intent.resource_id))
        session.stop_resource(intent.resource_id)  # explicit confirmation/release retry
    else:
        session.stop_resource(intent.resource_id)
        archive = owner.terminal_archive
        assert archive is not None and archive.run is run and len(archive.terminals) == 1
        terminal = archive.terminals[0]
        assert len(terminal.steps) == 1
        assert session.accept_paired_sweep(activation, bundles_from_paired_sweep(terminal)) == ()
        assert terminal.primary.state is SweepLineState.GAP
        assert owner.terminal_archive_error is None
    assert owner._live._pane_control_claim is None
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    assert session.accept_paired_sweep(activation, bundles_from_paired_sweep(prefix)) == ()
