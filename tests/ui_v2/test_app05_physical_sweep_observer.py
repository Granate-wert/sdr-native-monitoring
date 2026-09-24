"""Pure identity gates for the opt-in physical progressive-Sweep observer."""

from types import SimpleNamespace
import unittest

from scripts.benchmark_app05_physical_sweep_ui import (
    applied_matches_request, first_progressive_pair, parser, sweep_key,
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
        self.assertEqual(args.run_timeout, 35.0)
        self.assertEqual(args.theme, "dark")

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
            sample_rate_hz=61_440_000.0, fft_size=4096, backend=SimpleNamespace(value="cpu")))
        self.assertTrue(applied_matches_request(actual, sample_rate_msps=61.44, fft=4096))
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=62.0, fft=4096))
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=61.44, fft=8192))
        self.assertFalse(applied_matches_request(None, sample_rate_msps=61.44, fft=4096))
        actual.applied.backend.value = "cuda"
        self.assertFalse(applied_matches_request(actual, sample_rate_msps=61.44, fft=4096))

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
