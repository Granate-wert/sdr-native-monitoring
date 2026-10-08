"""Original HackRF Sweep service admission and creation journal; MOCK only."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.domain.processing_policy import HostDcMode, SdrProcessingPolicyV1
from tests.test_app07_hackrf_sweep_layer_bridge import configured
from tests.test_app07_product_layer_bridge import Kinds, batch, raw_ref
from tests.test_persistence_processing_metadata import density


CONTRACT = {"schema_version": 1, "scope": "native_contributing_sweep_metadata_only",
    "full_owner_context": False, "mixed_processing_line_refused": True,
    "string_max_bytes": 256, "segment_record_reserved_bytes": 1024}


def fixture(mode=HostDcMode.BLOCK_MEAN):
    service, native, control, exclusion, request, selection = configured()
    native.sweep_processing_metadata_contract = lambda: dict(CONTRACT)
    native.HACKRF_SWEEP_PROCESSING_FACTORY_CONTRACT_VERSION = 1
    request = replace(request, processing_policy=SdrProcessingPolicyV1(mode))
    return service, native, control, exclusion, request, selection


def publication(request, *, terminal=False, empty=False, creation=1):
    size, rate = request.fft_size, 20e6
    spacing = rate / size
    count = request.geometry.reduced.output_bins
    axis = request.start_hz + spacing * np.arange(1, count + 1)
    values = np.full(count, -100., dtype="float32")
    sources = np.floor((axis - request.start_hz) / 5e6).astype("int32")
    flags = np.full(count, 512 if request.processing_policy.dc_mode is HostDcMode.BLOCK_MEAN else 0,
                    dtype="uint32")
    acquired = () if empty else ((0, request.epoch),)
    if terminal and not empty:
        acquired = tuple((i, request.epoch) for i in range(4))
    missing = tuple(i for i in range(4) if i not in dict(acquired))
    mask = np.isin(sources, missing)
    sources[mask], values[mask], flags[mask] = -1, np.nan, 1 << 12
    for array in (axis, values, sources, flags):
        array.flags.writeable = False
    records = []
    for index, generation in acquired:
        p = density(request.processing_policy.dc_mode).processing_metadata
        p.center_frequency_hz = request.start_hz + (index // 4) * 20e6 + (index % 2) * 5e6 + 7.5e6
        p.sample_rate_hz, p.analog_bandwidth_hz = rate, 15e6
        p.fft_size = p.hop_size = size
        p.window, p.detector, p.precision_mode = "hann", "sample", "accurate_f32_f64_accum"
        p.fft_bin_width_hz, p.enbw_hz = spacing, 1.5 * rate / (size - 1)
        p.nominal_rbw_hz = p.enbw_hz
        records.append(SimpleNamespace(segment_index=index, config_generation=generation,
            frame_sequence=index, first_sample_index=index * 16379, timestamp_ns=100,
            sample_rate_hz=rate, fft_size=size, quality_flags=512 if not request.processing_policy.is_off else 0,
            processing_metadata=p))
    ref = raw_ref(kind=Kinds.SweepTerminal if terminal else Kinds.SweepProgress,
        sweep_epoch=request.epoch, line_sequence=1, revision=0 if terminal else 1,
        creation_sequence=creation, config_generation=0, update_sequence=0,
        source_frame_sequence=0, accumulation_sequence=0)
    raw = SimpleNamespace(source_id=request.source.device_id, epoch=request.epoch, line_sequence=1,
        unit="dBFS/bin", frequencies_hz=axis, values=values, quality_flags_per_bin=flags,
        source_segment_indices=sources, layer_ready=ref, segment_acquisition=records)
    if terminal:
        raw.physical_fft_size, raw.physical_fft_bin_width_hz = size, spacing
        raw.analysis_window_hz, raw.analysis_bins_per_usable_window = 5e6, size // 4
        raw.completed_ns, raw.state = 200, "gap" if missing else "complete"
        raw.gap_reasons = ("cancellation",) if missing else ()
        raw.missing_segment_indices = missing
        raw.segment_config_generations = tuple((i, request.epoch) for i in range(4))
    else:
        raw.revision, raw.acquired_segment_generations, raw.pending_segment_indices = 1, acquired, missing
    return raw, ref


class SweepProcessingOwnerTests(unittest.TestCase):
    def test_processed_contract_refused_before_identity_claim_clock_or_factory(self):
        for change in ({"schema_version": True}, {"full_owner_context": True}, {"extra": 1}):
            service, native, _, exclusion, request, selection = fixture()
            native.sweep_processing_metadata_contract = lambda: CONTRACT | change
            service._identity.observe = Mock()
            with self.subTest(change=change), self.assertRaises(LiveAdmissionRejected):
                service.start(request, selection)
            service._identity.observe.assert_not_called()
            native.analytical_ready_clock_ns.assert_not_called()
            native.create_hackrf_sweep_runtime_control.assert_not_called()
            self.assertEqual(exclusion.events, [])

    def test_original_progress_terminal_owner_receipts_and_default_off(self):
        for mode in HostDcMode:
            service, native, control, _, request, selection = fixture(mode)
            service.start(request, selection)
            self.assertEqual(native.create_hackrf_sweep_runtime_control.call_args.kwargs,
                {"layer_event_capacity": 64} | ({"dc_removal_block_mean": True} if mode is HostDcMode.BLOCK_MEAN else {}))
            for terminal in (False, True):
                raw, ref = publication(request, terminal=terminal, creation=2 if terminal else 1)
                control.poll_next_publication.return_value = raw
                control.drain_sweep_layer_ready_events.return_value = batch([ref], created=ref.creation_sequence)
                result = service.poll_latest()
                frame = result.line if terminal else result.progress
                self.assertIsNotNone(frame.processing_context)
                self.assertIs(frame.processing_context.layer_ready, frame.layer_ready)
                self.assertEqual(frame.processing_context.policy, request.processing_policy)
                self.assertEqual(frame.processing_context.resource_id, request.source.device_id)
                self.assertFalse(frame.values_db.flags.writeable)
                with self.assertRaises(ValueError):
                    replace(frame.processing_context, resource_id="foreign")
            control.drain_sweep_layer_ready_events.return_value = batch([], created=2, drained=2)
            service.stop()

    def test_processed_foreign_policy_geometry_journal_or_metadata_fail_closed(self):
        for defect in ("recipe", "center", "rate", "fft", "precision", "rbw", "quality", "unit", "grid", "source", "epoch", "metadata", "creation"):
            service, _, control, _, request, selection = fixture()
            service.start(request, selection)
            raw, ref = publication(request)
            m = raw.segment_acquisition[0].processing_metadata
            if defect == "recipe":
                m.dsp_processing_recipe = density(HostDcMode.OFF).processing_metadata.dsp_processing_recipe
            elif defect == "center":
                m.center_frequency_hz += 1
            elif defect == "rate":
                m.sample_rate_hz += 1
            elif defect == "fft":
                m.fft_size *= 2
            elif defect == "precision":
                m.precision_mode = "reference_f64"
            elif defect == "rbw":
                m.enbw_hz += 1
                m.nominal_rbw_hz = m.enbw_hz
            elif defect == "quality":
                raw.quality_flags_per_bin = raw.quality_flags_per_bin & np.uint32(~512 & 0xFFFFFFFF)
                raw.quality_flags_per_bin.flags.writeable = False
            elif defect == "unit":
                raw.unit = "dBFS/Hz"
            elif defect == "grid":
                raw.frequencies_hz = raw.frequencies_hz + 1
            elif defect == "source":
                raw.source_id = "foreign"
            elif defect == "epoch":
                raw.epoch += 1
            elif defect == "metadata":
                raw.segment_acquisition[0].processing_metadata = None
            elif defect == "creation":
                raw.layer_ready = SimpleNamespace(**vars(ref))
                raw.layer_ready.producer_instance_id += 1
            control.poll_next_publication.return_value = raw
            control.drain_sweep_layer_ready_events.return_value = batch([ref])
            with self.subTest(defect=defect), self.assertRaises(RuntimeError):
                service.poll_latest()
            self.assertIsNone(service._last_progress)
            control.drain_sweep_layer_ready_events.return_value = batch([], created=1, drained=1)
            service.stop()

    def test_confirmed_stop_terminal_cancellation_keeps_original_context_not_new_start(self):
        service, _, control, _, request, selection = fixture()
        service.start(request, selection)
        raw, ref = publication(request, terminal=True, empty=True)
        control.drain_sweep_layer_ready_events.return_value = batch([ref])
        service.stop()

        control.poll_next_publication.return_value = raw
        frame = service.poll_latest().line
        self.assertIsNotNone(frame.processing_context)
        self.assertEqual(frame.segment_acquisition, ())
        old_run = frame.layer_ready.owner_run_id
        service.start(replace(request, epoch=2), selection)
        self.assertNotEqual(old_run, service.layer_journal_snapshot().scope.owner_run_id)
        with self.assertRaises(RuntimeError):
            service.poll_latest()
        control.drain_sweep_layer_ready_events.return_value = batch([], created=0)
        service.stop()

    def test_host_processing_budget_is_refused_before_any_effect(self):
        service, native, _, exclusion, request, selection = fixture()
        service._identity.observe = Mock()
        with patch("sdr_monitor.services.hackrf_sweep_display.hackrf_sweep_processing_reserved_bytes", return_value=1 << 30):
            with self.assertRaises(LiveAdmissionRejected):
                service.start(request, selection)
        service._identity.observe.assert_not_called()
        native.analytical_ready_clock_ns.assert_not_called()
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        self.assertEqual(exclusion.events, [])
