"""Actual native MOCK HackRF owner + original product factory/service lifecycle.

Only the explicitly test-only runtime port replaces the vendor SDK. No real
device, DLL fallback, Python DSP or synthesized density/creation is allowed.
"""

import os
from pathlib import Path
import subprocess
import sys
import unittest


CODE = r'''
import importlib.util
from pathlib import Path
import sys
import time
from tests.native_test_dependencies import native_test_dll_directory
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.domain.processing_policy import DC_REMOVED_MASK, HostDcMode, SdrProcessingPolicyV1
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.hackrf_native_factory import HackrfNativeRuntimeFactory
from sdr_monitor.services.hackrf_product_live import HackrfProductLiveCoordinator
from sdr_monitor.services.native_layer_journal import LAYER_EVENT_CAPACITY
from sdr_monitor.services.native_owner_journal import EVENT_CAPACITY as OWNER_EVENT_CAPACITY

path, mode = Path(sys.argv[1]).resolve(strict=True), HostDcMode(sys.argv[2])
with native_test_dll_directory(str(path)):
    spec = importlib.util.spec_from_file_location("_sdr_native", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == path
    fixture = graph()
    controls, calls = [], []
    class ObservedOwner:
        def __init__(self, actual):
            self.actual, self.raw = actual, None
            controls.append(self)
        def __getattr__(self, name):
            return getattr(self.actual, name)
        def poll_persistence_snapshots(self, count):
            values = self.actual.poll_persistence_snapshots(count)
            if values:
                self.raw = values[-1]
            return values
    def create_mock(**values):
        # Original product native factory supplies these exact admitted values.
        calls.append(values)
        assert not values["rf_amplifier_enabled"] and not values["bias_tee_enabled"]
        assert values["fft_size"] == values["hop_size"] == 256
        assert values["slot_count"] == values["ready_capacity"] == 4
        assert values["dsp_output_capacity"] == values["presentation_capacity"] == 4
        assert values["window"] == native.WindowType.RECTANGULAR
        assert values["detector"] == native.DetectorType.SAMPLE
        assert values["persistence"].enabled and values["persistence"].power_bins == 16
        assert values["analytical_event_capacity"] == OWNER_EVENT_CAPACITY
        assert values["layer_event_capacity"] == LAYER_EVENT_CAPACITY
        assert values.get("dc_removal_block_mean", False) == (mode is HostDcMode.BLOCK_MEAN)
        return ObservedOwner(native._make_test_hackrf_runtime_dsp_control(4,
            layer_event_capacity=values["layer_event_capacity"],
            dc_removal_block_mean=values.get("dc_removal_block_mean", False),
            analytical_event_capacity=values["analytical_event_capacity"],
            configuration_generation=values["configuration_generation"], source_id=values["source_id"],
            center_frequency_hz=values["center_frequency_hz"], sample_rate_hz=values["sample_rate_hz"],
            baseband_filter_hz=values["baseband_filter_hz"]))
    # Explicit mock boundary; never call the actual official-SDK factory.
    native.create_hackrf_runtime_dsp_control = create_mock
    factory = HackrfNativeRuntimeFactory(lambda: native,
        analytical_event_capacity=OWNER_EVENT_CAPACITY, layer_event_capacity=LAYER_EVENT_CAPACITY)
    coordinator = HackrfProductLiveCoordinator(factory)
    service = HackrfAnalyzerService(native, fixture.live, fixture.catalog.snapshot, fixture.preflight, coordinator)
    try:
        fixture.application.discover()  # fixture-only capability provider
        fixture.application.select_device("source-hackrf")
        selection = fixture.application.current_source_selection()
        service.bind_selection(selection)
        request = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20,
            fft_size=256, hop_size=256, window="rectangular", source_id=selection.selected_id,
            slot_count=4, ready_capacity=4, dsp_output_capacity=4, presentation_capacity=4,
            persistence_enabled=True, persistence_mode="exponential-decay", persistence_power_bins=16,
            processing_policy=SdrProcessingPolicyV1(mode))
        service.stage(HackrfConfigurationPatch(request, selection.revision, service.current_snapshot().generation))
        assert not calls and fixture.live._external_analyzer_owner is None
        previous = None
        for run in range(2):
            assert service.start().error is None
            deadline = time.monotonic() + 3
            while True:
                snapshot = service.current_snapshot()
                assert snapshot.error is None, snapshot.error
                if snapshot.spectrum is not None and snapshot.persistence is not None:
                    break
                assert time.monotonic() < deadline, "actual MOCK density deadline"
                time.sleep(.001)
            density, spectrum = snapshot.persistence, snapshot.spectrum
            receipt = density.processing_context
            assert receipt is not None and receipt.family == "hackrf"
            assert receipt.layer_ready is density.layer_ready
            assert density.layer_ready.producer_instance_id != spectrum.detector_ready.producer_instance_id
            assert receipt.numerical_provenance.processing_recipe.dc_mode is mode
            assert bool(density.native_quality_flags & DC_REMOVED_MASK) == (mode is HostDcMode.BLOCK_MEAN)
            assert receipt.sample_rate.value_hz == request.sample_rate_hz
            assert not density.density.flags.writeable
            journal = service.density_layer_journal_snapshot()
            assert journal.state is LayerJournalState.ACTIVE
            assert journal.counters.producer_instance_id == receipt.layer_ready.producer_instance_id
            assert journal.scope.owner_run_id == spectrum.detector_ready.owner_run_id
            if previous is not None:
                assert receipt.layer_ready.owner_run_id != previous.layer_ready.owner_run_id
                assert receipt.layer_ready.producer_instance_id != previous.layer_ready.producer_instance_id
                assert receipt.layer_ready.identity.acquisition_epoch != previous.layer_ready.identity.acquisition_epoch
            stopped = service.stop()
            assert stopped.error is None, stopped.error
            assert service._poller is None and fixture.live._external_analyzer_owner is None
            assert not controls[-1].actual.metrics().lifecycle_open
            final = service.density_layer_journal_snapshot()
            assert final.state is LayerJournalState.FINAL and final.native_stop_confirmed
            assert final.counters.events_pending == 0
            if mode is HostDcMode.BLOCK_MEAN:
                try:
                    service._convert_persistence(controls[-1].raw, snapshot)
                except ValueError as error:
                    assert "ACTIVE SAME" in str(error), str(error)
                else:
                    raise AssertionError("FINAL owner authorized new processed density")
            previous = receipt
        assert len(calls) == len(controls) == 2
        assert fixture.observation.probes == fixture.observation.closes == 2
    finally:
        assert service.stop().error is None
        fixture.application.shutdown()
'''


class CompiledHackrfDensityOwnerTests(unittest.TestCase):
    def run_case(self, mode):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            self.skipTest("explicit matching native with test hooks required; no physical fallback")
        module = Path(selected).resolve(strict=True)
        result = subprocess.run([sys.executable, "-c", CODE, str(module), mode],
            cwd=Path(__file__).resolve().parents[1], env=dict(os.environ),
            capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_off_original_native_product_lifecycle(self):
        self.run_case("off")

    def test_block_mean_original_native_product_factory_owner_restart_stop(self):
        self.run_case("block_mean_v1")
