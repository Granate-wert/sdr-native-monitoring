"""Additive scalar surface checks, not RF/latency qualification."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("source_refill_calls", "source_refill_wait_ns", "source_canonicalization_ns",
          "source_inter_refill_gap_ns", "source_inter_refill_gap_count")


class SweepIngressTimingBindingTests(unittest.TestCase):
    def test_source_readonly_projection(self):
        source = (ROOT / "native/sdr_core/bindings/pluto_binding.cpp").read_text(encoding="utf-8")
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertEqual(source.count(f'.def_readonly("{field}", &sdr_pluto::ContinuousSweepCoordinatorMetrics::{field})'), 1)

    @unittest.skipUnless(os.environ.get("SDR_APP07_SWEEP_INGRESS_MODULE"), "explicit built-module path required")
    def test_actual_built_surface_no_hardware_open(self):
        path = Path(os.environ["SDR_APP07_SWEEP_INGRESS_MODULE"]).resolve(strict=True)
        spec = importlib.util.spec_from_file_location("_sdr_native", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        with os.add_dll_directory(str(path.parent)):
            spec.loader.exec_module(module)
        for field in FIELDS:
            self.assertTrue(hasattr(module.ContinuousSweepCoordinatorMetrics, field))


if __name__ == "__main__":
    unittest.main()
