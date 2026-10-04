"""Scalar projection contract checks; no SDR open and no RF qualification.

The explicit built-module check is opt-in. Real values are covered by C++ fake
runtime tests and separately attributed physical HIL, not by source inspection.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HackrfSweepMetricProjectionContractTests(unittest.TestCase):
    def test_exact_source_projection_does_not_infer_fft_from_blocks_or_crops(self):
        source = (ROOT / "native/sdr_core/bindings/hackrf_sweep_binding.cpp").read_text(encoding="utf-8")
        for key, value in (
            ("fft_frames_computed", "value.analysis.dsp.fft_frames_computed"),
            ("fft_frames_dropped", "value.analysis.dsp.fft_frames_dropped"),
            ("dsp_samples_processed", "value.analysis.dsp.samples_processed"),
            ("dsp_output_pending", "value.analysis.dsp.output_pending"),
            ("iq_payload_samples_accepted", "value.analysis.iq_payload_samples_accepted"),
        ):
            with self.subTest(key=key):
                self.assertEqual(source.count(f'result["{key}"] = {value};'), 1)
        self.assertIn('module.attr("HACKRF_SWEEP_METRICS_CONTRACT_VERSION") = 1;', source)
        self.assertIn('module.attr("HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION") = 1;', source)

    @unittest.skipUnless(os.environ.get("SDR_APP07_HF_METRICS_MODULE"), "explicit candidate module required")
    def test_compiled_additive_metrics_marker_no_hardware_open(self):
        path = Path(os.environ["SDR_APP07_HF_METRICS_MODULE"]).resolve(strict=True)
        self.assertEqual(path.suffix.casefold(), ".pyd")
        spec = importlib.util.spec_from_file_location("_sdr_native", path)
        assert spec is not None and spec.loader is not None
        native = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = native
        with os.add_dll_directory(str(path.parent)):
            spec.loader.exec_module(native)
        self.assertEqual(native.HACKRF_SWEEP_METRICS_CONTRACT_VERSION, 1)
        self.assertEqual(native.HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION, 1)
        self.assertTrue(callable(native.HackrfSweepRuntimeAnalysisControl.metrics))


if __name__ == "__main__":
    unittest.main()
