"""Compiled paired reduced Sweep boundary; explicit matching mock IIO only."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from tests.native_test_dependencies import explicit_native_dependencies

ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK = os.environ.get("SDR_APP07_TEST_SWEEP_MOCK", "")


@explicit_native_dependencies
def run_case(path: str, case: str) -> None:
    import ctypes
    import gc

    import numpy as np

    module_path = Path(path).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    assert spec is not None and spec.loader is not None
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == module_path
    assert native.PLUTO_PAIRED_SWEEP_REDUCED_PROTOCOL_VERSION == 1
    assert native.PLUTO_PAIRED_SWEEP_STATISTICS_PROTOCOL_VERSION == 1
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    hooks.mock_iio_set_phase_gate.argtypes = [ctypes.c_int, ctypes.c_longlong, ctypes.c_int]
    rx = native.PlutoReceiverSelection
    dsp = native.DspConfig(4096, 2048, native.WindowType.HANN, native.DetectorType.SAMPLE,
        native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64, 4, 1,
        8.6, native.CalibrationStatus.UNCALIBRATED, "", native.CONTRACT_SCHEMA_VERSION)

    def plan(selection, source, *, single=False, offset=0., bins=2048,
             epoch=91, rate=2000., queue=8, timeout=2000, statistics=None):
        segments = []
        for index in range(1 if single else 2):
            center = 2_440_000_000. + index * 30_000_000. + offset
            device = native.DeviceConfig(source, "usb:mock", center, 61_440_000., 56_000_000.,
                native.GainMode.MANUAL, 20., 0, 8192, native.CONTRACT_SCHEMA_VERSION)
            fixed = native.FixedBandConfig(device, dsp, backend=native.ComputeBackendKind.CPU,
                allow_runtime_fallback=False, snapshot_rate_hz=1., discard_blocks_after_start=0,
                receiver_selection=selection)
            segments.append(native.ContinuousSweepSegmentConfig(fixed, center - offset - 18_000_000., center - offset + 18_000_000.))
        return native.ContinuousSweepCoordinatorConfig(epoch, 2_422_000_000.,
            2_458_000_000. if single else 2_488_000_000., segments,
            output_queue_capacity=queue, segment_frame_timeout_ms=timeout, usable_window_hz=36_000_000.,
            analysis_bins_per_usable_window=bins, line_snapshot_rate_hz=rate, statistics=statistics)

    def refuses(call, error=native.ConfigurationError):
        try:
            call()
        except error:
            return
        raise AssertionError("invalid operation did not refuse")

    def immutable(obj, field, value):
        refuses(lambda: setattr(obj, field, value), AttributeError)

    def wait(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            assert time.monotonic() < deadline, "bounded mock operation timed out"
            time.sleep(.001)

    single = case in ("single", "stats-single")
    with_stats = case.startswith("stats-")
    stats = native.SweepStatisticsConfig(8, 8, -140., 20., 128 * 1024 * 1024, 64) if with_stats else None
    secondary_stats = native.SweepStatisticsConfig(8, 16, -140., 20., 128 * 1024 * 1024, 64) if with_stats else None
    queue = 1 if case in ("stats-retuning", "stats-single", "stats-optional") else 8
    rate = 1. if case == "stats-single" else 2000.
    config = native.PairedContinuousSweepCoordinatorConfig("admitted-resource",
        plan(rx.RX1, "opaque-left", single=single, queue=queue, rate=rate,
             statistics=None if case == "stats-optional" else stats),
        plan(rx.RX2, "opaque-right", single=single, queue=queue, rate=rate, statistics=secondary_stats))
    immutable(config, "resource_id", "changed")
    immutable(config.primary, "epoch", 0)
    # No callback/raw-IQ/test-control escape from this reduced polling surface.
    for name in ("StartAdmissionGate", "PairedSpectrumAnalyticalSink"):
        assert not hasattr(native, name)
    for name in ("push_iq", "refill", "set_start_delay_for_test", "start_pending_for_test",
                 "set_secondary_nan_step_for_test", "set_dsp_delay_for_test", "start_guarded"):
        assert not hasattr(native.NativeContinuousSweepCoordinator, name)
    for cls in (native.PairedSweepStepReceipt, native.PairedSweepLineFrame, native.PairedSweepProgressFrame):
        refuses(cls, TypeError)

    if case == "guards":
        before = hooks.mock_iio_rf_mutation_calls()
        for resource, secondary in (("", config.secondary), ("bad\0id", config.secondary),
                                    ("group", plan(rx.RX1, "opaque-right")),
                                    ("group", plan(rx.RX2, "opaque-left")),
                                    ("group", plan(rx.RX2, "opaque-right", offset=1.)),
                                    ("group", plan(rx.RX2, "opaque-right", bins=1024)),
                                    ("group", plan(rx.RX2, "opaque-right", epoch=92)),
                                    ("group", plan(rx.RX2, "opaque-right", rate=1000.)),
                                    ("group", plan(rx.RX2, "opaque-right", queue=4)),
                                    ("group", plan(rx.RX2, "opaque-right", timeout=1000)),
                                    ("group", plan(rx.RX2, "opaque-right", statistics=
                                        native.SweepStatisticsConfig(16, 32, -120., 0., 1)))):
            refuses(lambda: native.PairedContinuousSweepCoordinatorConfig(resource, config.primary, secondary))
        assert hooks.mock_iio_rf_mutation_calls() == before
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        large = native.SweepStatisticsConfig(8, 192, -140., 20., 128 * 1024 * 1024)
        # Individual configs are legal; combined pair must refuse before open.
        left, right = plan(rx.RX1, "opaque-left", statistics=large), plan(rx.RX2, "opaque-right", statistics=large)
        refuses(lambda: native.PairedContinuousSweepCoordinatorConfig("group", left, right))
        assert hooks.mock_iio_rf_mutation_calls() == before and hooks.mock_iio_live_contexts() == 0
        return

    before_contexts = hooks.mock_iio_created_contexts()
    before_lo = hooks.mock_iio_lo_write_calls()
    owner = native.NativeContinuousSweepCoordinator("usb:mock")
    saved_arrays = []
    saved_values = []

    def retain_statistics(frame):
        snapshot = frame.statistics
        assert snapshot is not None
        assert snapshot.source_id == frame.source.source_id and snapshot.epoch == frame.epoch
        immutable(snapshot, "unique_passes_seen", 0)
        for name in ("frequencies_hz", "average_db", "observations", "histogram_counts",
                     "density_frequency_edges_hz", "density_observations", "probability"):
            array = getattr(snapshot, name)
            assert not array.flags.writeable
            refuses(lambda: array.setflags(write=True), ValueError)
            saved_arrays.append(array)
            saved_values.append(array.copy())
        assert int(snapshot.observations.sum()) == int(snapshot.histogram_counts.sum()) == int(snapshot.density_observations.sum())
        assert np.all(np.isnan(snapshot.average_db[snapshot.observations == 0]))
        return snapshot
    try:
        writes = hooks.mock_iio_rf_mutation_calls()
        owner.configure_paired(config)
        assert owner.state() == native.EngineState.CONFIGURED
        assert hooks.mock_iio_rf_mutation_calls() == writes and hooks.mock_iio_live_buffers() == 0
        assert hooks.mock_iio_created_contexts() == before_contexts + 1
        for call in (owner.poll_lines, owner.poll_progress):
            refuses(call)
        assert owner.discard_lines() == 0  # Scalar drain supports pairs, but Start has not produced any.
        refuses(lambda current=owner: current.poll_paired_lines(-1), TypeError)
        if case in ("progress", "stats-progress"):
            hooks.mock_iio_set_phase_gate(1, 2_470_000_000, 1)
        owner.start()
        if case in ("progress", "stats-progress"):
            wait(lambda: hooks.mock_iio_phase_gate_entered() != 0)
            assert hooks.mock_iio_phase_gate_expired() == 0
            progress = owner.poll_paired_progress()
            assert progress is not None and progress.resource_id == "admitted-resource"
            assert progress.primary.epoch == progress.secondary.epoch >= 91
            assert progress.primary.line_sequence == progress.secondary.line_sequence
            assert len(progress.steps) == 1 and len(progress.primary.pending_segment_indices) == 1
            assert progress.primary.source.metadata_json["receiver_selection"] == '"RX1"'
            assert progress.secondary.source.metadata_json["receiver_selection"] == '"RX2"'
            assert not progress.primary.values.flags.writeable
            saved_arrays.append(progress.primary.values)
            saved_values.append(progress.primary.values.copy())
            if with_stats:
                for frame in (progress.primary, progress.secondary):
                    snapshot = retain_statistics(frame)
                    assert snapshot.unique_passes_seen == 1
                    assert np.any(snapshot.observations == 1) and np.any(snapshot.observations == 0)
            owner.request_stop()
            hooks.mock_iio_release_phase_gate()
            owner.join()
            assert owner.poll_paired_progress() is None
            gaps = owner.poll_paired_lines()
            assert len(gaps) == 1 and gaps[0].primary.state == gaps[0].secondary.state == "gap"
            assert "cancellation" in gaps[0].primary.gap_reasons
            if with_stats:
                for frame in (gaps[0].primary, gaps[0].secondary):
                    assert retain_statistics(frame).unique_passes_seen == 1  # terminal replaces partial pass
        elif case == "error":
            wait(lambda current=owner: current.metrics().has_error)
            owner.join()
            assert "LO" in owner.last_error() or "frequency" in owner.last_error()
            gaps = owner.poll_paired_lines()
            assert len(gaps) == 1 and gaps[0].primary.state == gaps[0].secondary.state == "gap"
            assert len(gaps[0].steps) == 1
            assert gaps[0].steps[0].center_frequency_hz == 2_440_000_000.
        elif with_stats:
            wait(lambda current=owner: current.metrics().has_error or current.metrics().completed_lines >= 20)
            assert not owner.metrics().has_error, owner.last_error()
            owner.stop()
            metrics = owner.metrics()
            lines = owner.poll_paired_lines()
            assert len(lines) == 1 and lines[0].primary.state == lines[0].secondary.state == "gap"
            pair = lines[0]
            right = retain_statistics(pair.secondary)
            assert right.unique_passes_seen == metrics.completed_lines + 1 > len(lines)
            assert right.retained_passes == 8
            if case == "stats-optional":
                assert pair.primary.statistics is None
            else:
                left = retain_statistics(pair.primary)
                assert left.unique_passes_seen == right.unique_passes_seen
                assert left.newest_pass_sequence == right.newest_pass_sequence and left.power_bins != right.power_bins
                finite = np.isfinite(left.average_db) & np.isfinite(right.average_db)
                assert np.any(np.abs(left.average_db[finite] - right.average_db[finite]) > .001)
            if single:
                assert metrics.line_cadence_snapshots_suppressed > 0
                assert metrics.completed_lines > metrics.output_queue.pushed and metrics.segment_reconfigurations == 1
            else:
                assert metrics.output_snapshots_superseded > 0
            epoch = pair.primary.epoch
            owner.configure_paired(config)
            assert not owner.poll_paired_lines() and owner.poll_paired_progress() is None
            owner.start()
            wait(lambda current=owner: current.metrics().has_error or current.metrics().completed_lines > 0)
            assert not owner.metrics().has_error, owner.last_error()
            owner.stop()
            fresh = owner.poll_paired_lines()[-1]
            assert fresh.secondary.statistics.epoch > epoch
            assert fresh.secondary.statistics.unique_passes_seen == owner.metrics().completed_lines + 1
        else:
            wait(lambda current=owner: current.metrics().has_error or current.metrics().completed_lines >= (8 if single else 3))
            assert not owner.metrics().has_error, owner.last_error()
            owner.stop()
            metrics = owner.metrics()
            assert metrics.analytical_fft_frames == metrics.secondary_analytical_fft_frames > 0
            assert hooks.mock_iio_lo_write_calls() - before_lo == metrics.segment_reconfigurations
            if single:
                assert metrics.segment_reconfigurations == 1, "single window retuned per line"
            first = owner.poll_paired_lines(1)
            assert len(first) == 1
            lines = first + owner.poll_paired_lines()
            complete = [line for line in lines if line.primary.state == "complete"]
            assert complete and len(lines) <= 8
            pair = complete[0]
            assert pair.resource_id == config.resource_id
            assert pair.primary.epoch == pair.secondary.epoch >= 91
            assert pair.primary.line_sequence == pair.secondary.line_sequence
            assert pair.primary.source.source_id == "opaque-left"
            assert pair.secondary.source.source_id == "opaque-right"
            assert pair.primary.source.metadata_json["receiver_selection"] == '"RX1"'
            assert pair.secondary.source.metadata_json["receiver_selection"] == '"RX2"'
            assert np.array_equal(pair.primary.frequencies_hz, pair.secondary.frequencies_hz)
            mask = np.isfinite(pair.primary.values) & np.isfinite(pair.secondary.values)
            assert np.any(np.abs(pair.primary.values[mask] - pair.secondary.values[mask]) > .001)
            assert len(pair.steps) == (1 if single else 2)
            for step, left, right in zip(pair.steps, pair.primary.segment_acquisition, pair.secondary.segment_acquisition):
                assert step.config_generation == left.config_generation == right.config_generation > 0
                assert step.frame_sequence == left.frame_sequence == right.frame_sequence
                assert step.first_sample_index == left.first_sample_index == right.first_sample_index
                assert step.timestamp_ns == left.timestamp_ns == right.timestamp_ns
                assert step.synchronization_epoch > 0
                assert step.center_frequency_hz == 2_440_000_000. + step.step_index * 30_000_000.
                assert (step.sample_rate_hz, step.analog_bandwidth_hz, step.fft_size) == (61_440_000., 56_000_000., 4096)
                immutable(step, "timestamp_ns", 0)
            immutable(pair, "resource_id", "changed")
            immutable(pair.primary.source, "source_id", "changed")
            for frame in (pair.primary, pair.secondary):
                for name in ("values", "frequencies_hz", "quality_flags_per_bin", "source_segment_indices"):
                    array = getattr(frame, name)
                    assert not array.flags.writeable
                    refuses(lambda: array.setflags(write=True), ValueError)
                    saved_arrays.append(array)
                    saved_values.append(array.copy())
            epoch = pair.primary.epoch
            owner.configure_paired(config)
            assert not owner.poll_paired_lines() and owner.poll_paired_progress() is None
            owner.start()
            wait(lambda current=owner: current.metrics().completed_lines > 0 or current.metrics().has_error)
            assert not owner.metrics().has_error, owner.last_error()
            owner.stop()
            rearmed = owner.poll_paired_lines()
            assert rearmed and all(item.primary.epoch == item.secondary.epoch > epoch for item in rearmed)
            owner.configure(config.primary)
            refuses(owner.poll_paired_lines)
            refuses(owner.poll_paired_progress)
    finally:
        hooks.mock_iio_release_phase_gate()
        if owner.state() in (native.EngineState.RUNNING, native.EngineState.STOPPING, native.EngineState.ERROR):
            owner.stop()
        owner.disconnect()
    del owner
    gc.collect()
    for array, expected in zip(saved_arrays, saved_values):
        assert np.array_equal(array, expected, equal_nan=True), "capsule array outlived native storage"
        assert not array.flags.writeable
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    print(f"paired Sweep reduced binding {case} PASS; mock ONLY")


@unittest.skipUnless(MODULE and MOCK and Path(MOCK).is_file(), "requires explicit matching native + mock")
class PairedSweepBindingTests(unittest.TestCase):
    def run_native(self, case: str) -> None:
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(Path(MOCK).resolve(strict=True))
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        if case == "error":
            environment["SDR_MOCK_LIBIIO_LO_WRITE_FAIL_AT_HZ"] = "2470000000"
        result = subprocess.run([sys.executable, "-c",
            "from tests.test_app07_paired_sweep_binding import run_case; import sys; run_case(sys.argv[1], sys.argv[2])",
            MODULE, case], cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validation_and_immutable_api(self) -> None:
        self.run_native("guards")

    def test_retuning_pair_and_rearm(self) -> None:
        self.run_native("retuning")

    def test_single_window_continuous_pair(self) -> None:
        self.run_native("single")

    def test_progress_before_complete_and_cancelled_prefix(self) -> None:
        self.run_native("progress")

    def test_firstcause_and_aligned_failed_prefix(self) -> None:
        self.run_native("error")

    def test_statistics_retuning_before_latestwins_and_lifetime(self) -> None:
        self.run_native("stats-retuning")

    def test_statistics_single_before_host_cadence(self) -> None:
        self.run_native("stats-single")

    def test_statistics_partial_progress_and_terminal_replacement(self) -> None:
        self.run_native("stats-progress")

    def test_statistics_optional_secondary_only(self) -> None:
        self.run_native("stats-optional")


if __name__ == "__main__":
    unittest.main()
