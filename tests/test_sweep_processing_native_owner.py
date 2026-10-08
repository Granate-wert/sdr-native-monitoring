"""Original HF Sweep service + real native control/FFT/journal, in-process MOCK SDK."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


CODE = r'''
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace
from tests.native_test_dependencies import native_test_dll_directory
from tests.test_app06_hackrf_sweep_display import setup_owner, SERIAL
from sdr_monitor.domain.processing_policy import HostDcMode, SdrProcessingPolicyV1, DC_REMOVED_MASK
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.services.hackrf_activation_preflight import HackrfRuntimeIdentityProbe
from sdr_monitor.services.hackrf_capability_adapter import HackrfBoardKind
from sdr_monitor.services.hackrf_sweep_display import HackrfSweepDisplayService

path, mode, cancel = Path(sys.argv[1]).resolve(strict=True), HostDcMode(sys.argv[2]), sys.argv[3] == "cancel"
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    fixture, _, _, exclusion, request, selection = setup_owner()
    request = replace(request, stop_hz=140_000_000, processing_policy=SdrProcessingPolicyV1(mode))
    owners, emitters, calls = [], [], []
    class Observed:
        def __init__(self, actual): self.actual, self.raw = actual, None
        def __getattr__(self, name): return getattr(self.actual, name)
        def poll_next_publication(self):
            self.raw = self.actual.poll_next_publication()
            return self.raw
    def create_mock(serial, source, epoch, fft, start, stop, lna, vga, **extra):
        assert serial == SERIAL and lna == 16 and vga == 20
        assert source == request.source.device_id and fft == request.fft_size
        assert start == 100 and stop == 140
        assert extra == {"layer_event_capacity": 64} | ({"dc_removal_block_mean": True} if mode is HostDcMode.BLOCK_MEAN else {})
        calls.append((epoch, extra))
        actual, emit = native._make_test_hackrf_sweep_runtime_control(source, epoch, fft, start, stop,
            dc_removal_block_mean=extra.get("dc_removal_block_mean", False))
        owners.append(Observed(actual)); emitters.append(emit)
        return owners[-1]
    native.create_hackrf_sweep_runtime_control = create_mock  # Explicit no-vendor boundary.
    identity = lambda: SimpleNamespace(probe=lambda: HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, SERIAL),
        close=lambda: None)
    service = HackrfSweepDisplayService(native, fixture._catalog, exclusion, identity, fixture._manifest)
    previous = None
    try:
        service.preflight(request, selection)
        assert not calls and not exclusion.events
        for attempt in (1, 2):
            current = replace(request, epoch=attempt)
            service.start(current, selection)
            assert owners[-1].actual.metrics()["lifecycle_open"]
            emitters[-1](0, 1)
            deadline = time.monotonic() + 3
            while True:
                try:
                    snapshot = service.poll_latest()
                except Exception as error:
                    traceback.print_exception(error.__context__)
                    from sdr_monitor.services.native_continuous_sweep import _to_domain_acquisition
                    print(_to_domain_acquisition(owners[-1].raw), flush=True)
                    raise
                if snapshot.progress is not None: break
                assert time.monotonic() < deadline, "original MOCK progressive deadline"
                time.sleep(.002)
            frame = snapshot.progress
            assert snapshot.line is None and frame.revision == 2
            context = frame.processing_context
            assert context is not None and context.policy == request.processing_policy
            assert context.layer_ready is frame.layer_ready and context.rf_authority.value == "sdk_applied_not_readback"
            assert bundle_from_sweep(frame).spectrum is frame
            assert len(frame.segment_acquisition) == 2
            assert frame.segment_acquisition[0].processing_metadata.center_frequency_hz == 107_500_000
            assert frame.segment_acquisition[1].processing_metadata.center_frequency_hz == 107_500_000
            assert all(bool(r.quality_flags & DC_REMOVED_MASK) == (mode is HostDcMode.BLOCK_MEAN)
                       for r in frame.segment_acquisition)
            assert service.layer_journal_snapshot().state is LayerJournalState.ACTIVE
            assert not frame.values_db.flags.writeable
            if previous is not None:
                assert context.layer_ready.owner_run_id != previous.owner_run_id
                assert context.layer_ready.producer_instance_id != previous.producer_instance_id
                assert context.layer_ready.identity.epoch != previous.identity.epoch
            previous = context.layer_ready
            if not cancel:
                emitters[-1](1, 3)
                deadline = time.monotonic() + 3
                while True:
                    result = service.poll_latest()
                    if result.line is not None: break
                    assert time.monotonic() < deadline, "original MOCK terminal deadline"
                    time.sleep(.002)
                assert result.line.state.value == "complete" and len(result.line.segment_acquisition) == 8
                assert result.line.processing_context.policy == request.processing_policy
            service.stop()
            assert service.layer_journal_snapshot().state is LayerJournalState.FINAL
            assert service.layer_journal_snapshot().native_stop_confirmed
            assert owners[-1].actual.metrics()["worker_joined"] and not owners[-1].actual.metrics()["lifecycle_open"]
            if cancel:
                result = service.poll_latest()
                assert result.line is not None and result.line.state.value == "gap"
                assert len(result.line.segment_acquisition) == 2 and len(result.line.missing_segment_indices) == 6
                assert result.line.processing_context.policy == request.processing_policy
                assert result.line.layer_ready.owner_run_id == previous.owner_run_id
            try: emitters[-1](0, 1)
            except native.ConfigurationError: pass
            else: raise AssertionError("stopped native MOCK accepted callback injection")
        assert len(calls) == 2 and [v[0] for v in exclusion.events] == ["claim", "release", "claim", "release"]
    finally:
        service.close()
'''


class CompiledSweepProcessingOwnerTests(unittest.TestCase):
    def run_case(self, mode, cancel=False):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            self.skipTest("explicit matching test native required; no physical fallback")
        result = subprocess.run([sys.executable, "-c", CODE, str(Path(selected).resolve(strict=True)), mode,
            "cancel" if cancel else "complete"], cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ), capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_off_original_native_sweep_progress_terminal_stop_rearm(self):
        self.run_case("off")

    def test_block_mean_original_native_sweep_progress_terminal_stop_rearm(self):
        self.run_case("block_mean_v1")

    def test_block_mean_original_native_cancel_partial_stop_rearm(self):
        self.run_case("block_mean_v1", True)
