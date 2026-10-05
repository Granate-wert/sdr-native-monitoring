"""Single Sweep SAME-owner integration and full-coverage budget; MOCK only."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.layer_journal import SweepLayerScope, LayerJournalState
from sdr_monitor.domain.layer_ready import SweepLayerIdentity
from sdr_monitor.services.native_layer_journal import NativeLayerJournal, sweep_layer_host_reservation
from sdr_monitor.services.native_owner_journal import _scalar_bytes
from sdr_monitor.services.native_sweep_layer_journal import (
    sweep_layer_reserved_bytes, pluto_sweep_layer_capacity, preflight_sweep_layer_config,
)
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from tests.test_app07_product_layer_bridge import setup, raw_ref, batch, Kinds

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "native/sdr_core/out/build/rtl-hf/libiio.dll"


class SweepJournalBudgetTests(unittest.TestCase):
    def test_full_plan_preflight_and_refusal_before_native_configure(self):
        native, _, _, _, _ = setup()
        native.PlutoReceiverSelection = SimpleNamespace(RX1=1, RX2=2)
        native.NativeContinuousSweepCoordinator = SimpleNamespace(drain_sweep_layer_ready_events=Mock())
        native.ContinuousSweepCoordinatorConfig = SimpleNamespace(layer_event_capacity=object())
        self.assertEqual(pluto_sweep_layer_capacity(native), 64)
        fixed = SimpleNamespace(device=SimpleNamespace(source_id="source", sample_rate_hz=1024.),
            dsp=SimpleNamespace(fft_size=256), receiver_selection=1)
        segment = SimpleNamespace(fixed_band=fixed)
        config = SimpleNamespace(epoch=0, layer_event_capacity=64, segments=(segment,) * 2048,
            usable_window_hz=512., analysis_bins_per_usable_window=0, output_queue_capacity=1,
            display_start_hz=1e6, display_stop_hz=1e6 + 512 * 2048)
        self.assertEqual(preflight_sweep_layer_config(config), 2048)
        owner = SimpleNamespace(configure=Mock(), start=Mock(), disconnect=Mock())
        with patch("sdr_monitor.services.native_continuous_sweep.create_identity_bound_owner", return_value=owner):
            service = NativeContinuousSweepDisplayService(native, "usb:mock")
        config.output_queue_capacity = 64
        with self.assertRaisesRegex(ValueError, "memory budget"):
            service.start(config)
        owner.configure.assert_not_called()
        owner.start.assert_not_called()
        self.assertFalse(service._stop_required)
        service.close()
        native.LAYER_CREATION_CONTRACT_VERSION = True
        self.assertEqual(pluto_sweep_layer_capacity(native), 0)

    def test_full_2048_coverage_authenticated_inside_explicit_sweep_reservation(self):
        native, bridge, base, _, _ = setup()
        scope = SweepLayerScope(base.clock_scope_id, base.host_process_id, base.owner_run_id,
            base.source_id, base.receiver_id, base.session_id, 0)
        for pending_count in (0, 1, 1024, 2047):
            acquired = tuple((i, i + 1) for i in range(2048 - pending_count))
            pending = tuple(range(len(acquired), 2048))
            revision = len(acquired) if pending_count else None
            key = SweepLayerIdentity("source", 0, 3, revision, acquired, pending, "RX2")
            ref = raw_ref(kind=Kinds.SweepProgress if pending_count else Kinds.SweepTerminal,
                sweep_epoch=0, line_sequence=3, revision=revision or 0,
                config_generation=0, update_sequence=0, source_frame_sequence=0, accumulation_sequence=0)
            journal = NativeLayerJournal(native, 64, density=False, sweep_segments=2048)
            journal.begin(scope)
            journal.drain(lambda _: batch([ref]))
            result = journal.receipt(ref, key, bridge)
            self.assertIsNotNone(result)
            self.assertEqual(result.identity, key)
            self.assertEqual(result.owner_run_id, scope.owner_run_id)
            self.assertGreater(_scalar_bytes(result), 4096)
            self.assertLess(_scalar_bytes(result), journal._receipt_budget)
            self.assertEqual(journal._host_budget, sweep_layer_host_reservation(2048))
            self.assertLess(journal._host_budget, sweep_layer_reserved_bytes(2048))
            journal.finish(lambda _: batch([], created=1, drained=1))
            self.assertIs(journal.current().state, LayerJournalState.FINAL)
            self.assertIsNone(journal.receipt(ref, replace(key, receiver_id="RX1"), bridge))

    def test_scope_has_no_invented_global_generation_and_budgets_are_strict(self):
        _, bridge, base, _, _ = setup()
        scope = SweepLayerScope(bridge.clock_scope_id, bridge.host_process_id, "run", "source", "RX1", "run", 0)
        self.assertFalse(hasattr(scope, "configuration_generation"))
        for invalid in (0, True, 2049, 1.5):
            with self.assertRaises(ValueError):
                sweep_layer_host_reservation(invalid)
        with self.assertRaises(ValueError):
            replace(scope, receiver_id="RX0")
        native, _, _, density, _ = setup()
        with self.assertRaises(ValueError):
            density.begin(scope)
        self.assertIsNotNone(base)
        self.assertIsNotNone(native)


CODE = r'''
import ctypes, importlib.util, os, sys, time, traceback
from pathlib import Path
from unittest.mock import patch
from tests.native_test_dependencies import native_test_dll_directory
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from sdr_monitor.services.native_sweep_layer_journal import pluto_sweep_layer_capacity
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.domain.analytical_ready import ReadyClockMapping

path, case = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    assert pluto_sweep_layer_capacity(native) == 64
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    def fixed(center):
        device = native.DeviceConfig("sweep-source", "usb:mock", center, 3_000_000.,
            1_500_000., native.GainMode.MANUAL, 20., 0, 4096, 5)
        dsp = native.DspConfig(1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
            native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
            4, 1, 8.6, native.CalibrationStatus.UNCALIBRATED, "", 5)
        return native.FixedBandConfig(device, dsp, snapshot_rate_hz=120., discard_blocks_after_start=1)
    parts = [native.ContinuousSweepSegmentConfig(fixed(2449500000.), 2449000000., 2450100000.),
             native.ContinuousSweepSegmentConfig(fixed(2450500000.), 2449900000., 2451000000.)]
    if case == "one-window":
        parts = [native.ContinuousSweepSegmentConfig(fixed(2450000000.), 2449500000., 2450500000.)]
    config = native.ContinuousSweepCoordinatorConfig(72, parts[0].usable_start_hz,
        parts[-1].usable_stop_hz, parts, output_queue_capacity=4,
        segment_frame_timeout_ms=1000, line_snapshot_rate_hz=2000.,
        layer_event_capacity=0 if case == "disabled" else 64)
    if case == "factory":
        from sdr_monitor.domain import BackendKind, LiveConfiguration
        from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
        from sdr_monitor.services.native_sweep import NativeSweepLease, NativeSweepSource
        from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
        from sdr_monitor.services.native_sweep_layer_journal import sweep_layer_reserved_bytes
        profile = LiveConfiguration(center_hz=2450e6, sample_rate_hz=3e6,
            analog_bandwidth_hz=1.5e6, fft_size=1024, backend=BackendKind.CPU, averaging_frames=1)
        source = NativeSweepSource("usb:mock", "sweep-source", profile)
        factory = NativeContinuousSweepPlanFactory(NativeSweepLease(native, source, lambda: None, lambda: None))
        request = ContinuousSweepPlanRequest(2449e6, 2451e6, usable_window_hz=1.5e6,
            overlap_hz=.2e6, epoch=72, acquisition_buffer_samples=4096, line_snapshot_rate_hz=120.)
        base = factory.preflight_native_profile(native, profile, request)
        planned = factory.preflight(request)
        assert planned.reduced.total_bytes == base.reduced.total_bytes + sweep_layer_reserved_bytes(planned.segment_count)
        config = factory.build(request)
        assert config.layer_event_capacity == 64
        assert all(s.fixed_band.layer_event_capacity == 0 for s in config.segments)
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        factory.close()
    actual = native.NativeContinuousSweepCoordinator("usb:mock")
    calls, raw_lines, raw_progress = [], {}, {}
    class Owner:
        def __getattr__(self, name): return getattr(actual, name)
        def start(self):
            if case == "start-failure" and not calls:
                calls.append("start-refused")
                raise RuntimeError("injected before native Start")
            actual.start()
        def poll_lines(self):
            values = actual.poll_lines()
            raw_lines.update({v.line_sequence: v for v in values})
            return values
        def poll_progress(self):
            value = actual.poll_progress()
            if value is not None: raw_progress[(value.line_sequence, value.revision)] = value
            return value
        def drain_sweep_layer_ready_events(self, receiver, maximum):
            calls.append((receiver, maximum, actual.state()))
            if case == "journal-failure": raise RuntimeError("optional journal failure")
            return actual.drain_sweep_layer_ready_events(receiver, maximum)
    with patch("sdr_monitor.services.native_continuous_sweep.create_identity_bound_owner", return_value=Owner()):
        service = NativeContinuousSweepDisplayService(native, "usb:mock")
    previous = None
    try:
        if case == "start-failure":
            try: service.start(config)
            except RuntimeError as e: assert "injected" in str(e)
            else: raise AssertionError("activation failure not propagated")
            service.stop()
            assert service.layer_journal_snapshot().native_stop_confirmed
        for run in range(2):
            service.start(config)
            saw_line = saw_progress = saw_receipt = False
            first_sequence = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = service.poll_latest()
                for frame in (snapshot.line, snapshot.progress):
                    if frame is None: continue
                    if hasattr(frame, "revision"):
                        saw_progress = True
                        original = raw_progress[(frame.sequence, frame.revision)]
                    else:
                        saw_line = True
                        original = raw_lines[frame.sequence]
                        assert frame.completed_at_ns == original.completed_ns
                    if case == "disabled":
                        assert frame.layer_ready is None and frame.receiver_id is None
                    elif case == "journal-failure":
                        assert frame.layer_ready is None
                    elif frame.layer_ready is not None:
                        ref = frame.layer_ready
                        state = service.layer_journal_snapshot()
                        assert ref.owner_run_id == state.scope.owner_run_id
                        assert ref.session_id == state.scope.session_id
                        assert ref.ready_native_ns == original.layer_ready.ready_native_ns
                        assert ref.creation_sequence == original.layer_ready.creation_sequence
                        assert ref.identity.receiver_id == "RX1"
                        assert ref.producer_instance_id == state.counters.producer_instance_id
                        assert ref.mapping is ReadyClockMapping.BOUNDED
                        saw_receipt = True
                    first_sequence = frame.sequence if first_sequence is None else first_sequence
                if saw_line and (saw_progress or case == "one-window") and (
                        saw_receipt or case in ("disabled", "journal-failure")): break
                time.sleep(.001)
            assert saw_line and (saw_progress or case == "one-window"), (case, saw_line, saw_progress)
            if case not in ("disabled", "journal-failure"): assert saw_receipt
            before = len(calls), service.layer_journal_snapshot(), native.analytical_ready_clock_ns()
            for _ in range(20): assert service.layer_journal_snapshot() is before[1]
            assert len(calls) == before[0]
            service.stop()
            final = service.layer_journal_snapshot()
            assert final.native_stop_confirmed
            if case == "disabled":
                assert final.state is LayerJournalState.UNSUPPORTED and final.counters is None and not calls
            elif case == "journal-failure": assert final.state is LayerJournalState.INCOMPLETE
            else:
                assert final.state is LayerJournalState.FINAL and final.counters.events_pending == 0
                if previous is not None:
                    assert final.scope.owner_run_id != previous.scope.owner_run_id
                    assert final.counters.producer_instance_id != previous.counters.producer_instance_id
            service.poll_latest() # stopped terminal still valid, not a fabricated fresh run
            previous = final
    except BaseException:
        traceback.print_exc()
        raise
    finally: service.close()
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
'''


class CompiledSweepOwnerTests(unittest.TestCase):
    def test_actual_owner_retunes_one_window_disabled_failure_and_rearm(self):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected or not MOCK.is_file():
            self.skipTest("explicit matching native/mock required; no physical fallback")
        for case in ("retunes", "one-window", "disabled", "journal-failure", "start-failure", "factory"):
            with self.subTest(case=case):
                environment = dict(os.environ)
                environment.update(LIBIIO_DLL_PATH=str(MOCK), SDR_MOCK_LIBIIO_REFILL_DELAY_MS="20")
                result = subprocess.run([sys.executable, "-c", CODE,
                    str(Path(selected).resolve(strict=True)), case], cwd=ROOT, env=environment,
                    capture_output=True, text=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
