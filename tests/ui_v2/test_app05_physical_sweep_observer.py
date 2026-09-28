"""Pure identity gates for the opt-in physical progressive-Sweep observer."""

from types import SimpleNamespace
import unittest
import numpy as np

from scripts.benchmark_app05_physical_sweep_ui import (
    applied_matches_request, complete_quality_summary, discovered_usb_device_id, first_progressive_pair,
    native_plan_geometry, native_stage_summary, parser, stage_rows, sweep_key,
    terminal_gap_paints, uploaded_waterfall_key, waterfall_update_key,
)


def events(sequence: int = 7, *, partial_ns: int = 10, model_ns: int = 20,
           complete_ns: int = 30):
    source = "continuous-sweep:pluto"
    partial = [source, 3, sequence, "partial", 2]
    complete = [source, 3, sequence, "complete", 0]
    paints = [dict(pane=pane, key=partial, when_ns=partial_ns, coverage_runs=1)
              for pane in ("spectrum", "waterfall")]
    paints += [dict(pane=pane, key=complete, when_ns=complete_ns, coverage_runs=1)
               for pane in ("spectrum", "waterfall")]
    return paints, {(source, 3, sequence): model_ns}


class PhysicalSweepObserverTests(unittest.TestCase):
    def test_parser_uses_explicit_uri_and_normal_bounded_geometry(self):
        args = parser().parse_args(["--uri", "usb:3.12.5", "--output", "evidence.json"])
        self.assertEqual((args.start_mhz, args.stop_mhz), (2300.0, 2600.0))
        self.assertEqual((args.sample_rate_msps, args.fft), (61.44, 4096))
        self.assertIsNone(args.rf_bandwidth_mhz)
        self.assertEqual(args.speed_profile, "averaged")
        self.assertFalse(args.select_discovered_usb)
        self.assertFalse(args.require_native_stage_timings)
        self.assertIsNone(args.acquisition_buffer_samples)
        self.assertEqual(args.run_timeout, 35.0)
        self.assertEqual(args.theme, "dark")
        selected = parser().parse_args(["--uri", "usb:3.4.5", "--output", "evidence.json",
                                        "--rf-bandwidth-mhz", "50", "--speed-profile", "quick"])
        self.assertEqual((selected.rf_bandwidth_mhz, selected.speed_profile), (50, "quick"))

    def test_discovered_usb_selection_requires_one_exact_ad936x_route(self):
        choices = (SimpleNamespace(uri="usb:3.4.5", alternate_uris=(), device_id="pluto:one"),
                   SimpleNamespace(uri="usb:3.6.7", alternate_uris=(), device_id="pluto:two"))
        self.assertEqual(discovered_usb_device_id(choices, "usb:3.4.5"), "pluto:one")
        self.assertIsNone(discovered_usb_device_id(choices, "usb:3.9.9"))
        with self.assertRaises(ValueError):
            discovered_usb_device_id(choices + (choices[0],), "usb:3.4.5")
        with self.assertRaises(ValueError):
            discovered_usb_device_id((SimpleNamespace(uri="usb:3.4.5", alternate_uris=(),
                                                       device_id="hackrf:one"),), "usb:3.4.5")

    def test_native_stage_summary_requires_consistent_successful_phase_counts(self):
        timing = SimpleNamespace(count=10, total_ns=1000, max_ns=200)
        raw = SimpleNamespace(segment_stop_timing=timing, segment_configure_timing=timing,
                              segment_start_timing=timing, segment_frame_wait_timing=timing,
                              segment_reconfigurations=10, segment_frame_timeouts=0,
                              completed_lines=1, gapped_lines=1, device_iq_samples=100,
                              device_iq_blocks=2, analytical_fft_frames=3, source_short_reads=0,
                              source_refill_errors=0, source_estimated_dropped_samples=0,
                              acquisition_queue_blocks_dropped=0, fft_frames_dropped=0)
        summary = native_stage_summary(raw)
        self.assertEqual(summary["stages"]["segment_frame_wait_timing"]["count"], 10)
        self.assertEqual(summary["scalars"]["device_iq_samples"], 100)
        raw.segment_reconfigurations = 9
        with self.assertRaises(ValueError):
            native_stage_summary(raw)
        raw.segment_reconfigurations = 10
        raw.segment_frame_wait_timing = SimpleNamespace(count=10, total_ns=100, max_ns=200)
        with self.assertRaises(ValueError):
            native_stage_summary(raw)

    def test_native_plan_geometry_requires_same_buffer_and_discard_for_all_segments(self):
        def segment(buffer_samples):
            fixed = SimpleNamespace(device=SimpleNamespace(buffer_samples=buffer_samples,
                                                           sample_rate_hz=61.44e6),
                                    discard_blocks_after_start=2,
                                    dsp=SimpleNamespace(fft_size=4096))
            return SimpleNamespace(fixed_band=fixed)
        summary = native_plan_geometry(SimpleNamespace(segments=(segment(65_536), segment(65_536))))
        self.assertEqual((summary["buffer_samples"], summary["discard_blocks_after_start"]), (65_536, 2))
        with self.assertRaises(ValueError):
            native_plan_geometry(SimpleNamespace(segments=(segment(65_536), segment(262_144))))

    def test_complete_quality_summary_keeps_only_scalar_coverage_and_seams(self):
        line = SimpleNamespace(state=SimpleNamespace(value="complete"),
                               frequencies_hz=np.array([1., 2., 3., 4.]),
                               values_db=np.array([-90., -91., -92., -93.]),
                               quality_flags=np.array([0x2001] * 4, dtype=np.uint16),
                               source_segment_indices=np.array([0, 0, 1, 1]),
                               missing_segment_indices=(), gap_reasons=(), unit="dBFS/bin",
                               segment_acquisition=(object(), object()))
        summary = complete_quality_summary(line)
        self.assertEqual((summary["bin_count"], summary["finite_bins"],
                          summary["seam_boundary_count"], summary["acquisition_records"]), (4, 4, 1, 2))
        self.assertEqual(summary["quality_mask_or"], 0x2001)
        self.assertEqual(summary["seam_jump_max_db"], 1.0)
        line.state.value = "gap"
        with self.assertRaises(ValueError):
            complete_quality_summary(line)
        line.state.value = "complete"
        line.values_db[2] = np.nan
        with self.assertRaises(ValueError):
            complete_quality_summary(line)

    def test_frame_key_keeps_source_epoch_pass_and_revision(self):
        progress = SimpleNamespace(source_id="s", epoch=3, sequence=7, revision=2)
        complete = SimpleNamespace(source_id="s", epoch=3, sequence=7,
                                   state=SimpleNamespace(value="complete"))
        self.assertEqual(sweep_key(progress), ("s", 3, 7, "partial", 2))
        self.assertEqual(sweep_key(complete), ("s", 3, 7, "complete", 0))
        self.assertIsNone(sweep_key(SimpleNamespace(source_id="s", epoch=None, sequence=7)))

    def test_waterfall_key_uses_row_generation_not_sequence_only(self):
        stamp = SimpleNamespace(sequence=7, revision=2, state=SimpleNamespace(value="partial"))
        row = SimpleNamespace(configuration_generation='sweep:["physical:rx-2", 17]')
        update = SimpleNamespace(row=row, stamp=stamp)
        expected = ("physical:rx-2", 17, 7, "partial", 2)
        self.assertEqual(waterfall_update_key(update), expected)
        self.assertIsNone(waterfall_update_key(SimpleNamespace(
            row=SimpleNamespace(configuration_generation="sweep:invalid"), stamp=stamp)))
        self.assertIsNone(waterfall_update_key(SimpleNamespace(
            row=SimpleNamespace(configuration_generation='sweep:["physical:rx-2", true]'), stamp=stamp)))

    def test_waterfall_paint_requires_matching_successful_upload(self):
        current = ("physical:rx-2", 17, 7, "partial", 2)
        other_epoch = ("physical:rx-2", 16, 7, "partial", 2)
        stamp = SimpleNamespace(sequence=7, revision=2, state=SimpleNamespace(value="partial"))
        self.assertEqual(uploaded_waterfall_key(
            attempted=current, uploaded=current, stamp=stamp, uploads=8, upload_count=8), current)
        self.assertIsNone(uploaded_waterfall_key(
            attempted=current, uploaded=other_epoch, stamp=stamp, uploads=8, upload_count=8))
        self.assertIsNone(uploaded_waterfall_key(
            attempted=current, uploaded=current, stamp=stamp, uploads=7, upload_count=8))
        self.assertIsNone(uploaded_waterfall_key(
            attempted=current, uploaded=current,
            stamp=SimpleNamespace(sequence=7, revision=3, state=SimpleNamespace(value="partial")),
            uploads=8, upload_count=8))

    def test_applied_cpu_configuration_must_match_request(self):
        actual = SimpleNamespace(applied=SimpleNamespace(
            sample_rate_hz=61_440_000.0, analog_bandwidth_hz=50_000_000.0,
            fft_size=4096, backend=SimpleNamespace(value="cpu")))
        self.assertTrue(applied_matches_request(actual, sample_rate_msps=61.44, fft=4096))
        self.assertTrue(applied_matches_request(actual, sample_rate_msps=61.44, fft=4096,
                                                rf_bandwidth_mhz=50.0))
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=61.44, fft=4096,
                                                 rf_bandwidth_mhz=56.0))
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=62.0, fft=4096))
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=61.44, fft=8192))
        self.assertFalse(applied_matches_request(None, sample_rate_msps=61.44, fft=4096))
        actual.applied.backend.value = "cuda"
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=61.44, fft=4096))

    def test_host_to_paint_join_uses_exact_identity_and_monotone_stage_order(self):
        key = ("s", 3, 7, "partial", 2)
        painted = [dict(pane="spectrum", key=list(key), when_ns=8_000_000),
                   dict(pane="waterfall", key=list(key), when_ns=9_000_000),
                   dict(pane="spectrum", key=["s", 4, 7, "partial", 2], when_ns=9_000_000)]
        polls = {key: dict(returned_ns=1_000_000)}
        rows = stage_rows(painted, polls, {key: 2_000_000}, {key: 4_000_000})
        self.assertEqual([row["pane"] for row in rows], ["spectrum", "waterfall"])
        self.assertEqual(rows[0]["poll_return_to_paint_ms"], 7.0)
        self.assertEqual(rows[1]["model_to_paint_ms"], 5.0)
        self.assertEqual(stage_rows(painted, polls, {key: 0}, {key: 4_000_000}), [])

    def test_pair_requires_both_canvases_and_same_pass_before_terminal_model(self):
        paints, models = events()
        pair = first_progressive_pair(paints, models)
        self.assertIsNotNone(pair)
        self.assertEqual((pair["epoch"], pair["sequence"], pair["revision"]), (3, 7, 2))
        self.assertIsNone(first_progressive_pair(paints[:3], models))
        other = [dict(item, key=item["key"][:2] + [8] + item["key"][3:])
                 if item["pane"] == "waterfall" else item for item in paints]
        self.assertIsNone(first_progressive_pair(other, models))

    def test_pair_rejects_late_partial_or_no_coverage(self):
        paints, models = events(partial_ns=25, model_ns=20)
        self.assertIsNone(first_progressive_pair(paints, models))
        paints, models = events()
        paints[0]["coverage_runs"] = 0
        self.assertIsNone(first_progressive_pair(paints, models))

    def test_terminal_gap_requires_exact_pass_and_both_post_stop_paints(self):
        gap = ("s", 3, 8, "gap", 0)
        paints = [dict(pane=pane, key=list(gap), when_ns=30, coverage_runs=0)
                  for pane in ("spectrum", "waterfall")]
        self.assertEqual(set(terminal_gap_paints(paints, gap, 20)), {"spectrum", "waterfall"})
        self.assertIsNone(terminal_gap_paints(paints, gap, 35))
        self.assertIsNone(terminal_gap_paints(paints, ("s", 3, 7, "gap", 0), 20))
        self.assertIsNone(terminal_gap_paints(paints[:1], gap, 20))


if __name__ == "__main__":
    unittest.main()
