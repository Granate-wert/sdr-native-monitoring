"""Explicit matching native: paired burst admission/readonly reduced boundary.

No acquisition owner, raw Python input, physical RX2 or UI admission proof.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")


@unittest.skipUnless(MODULE, "requires explicit matching native module")
class DualRxBurstBindingTests(unittest.TestCase):
    def test_legacy_constructor_budget_factory_readonly_and_no_raw_boundary(self) -> None:
        code = r'''
import importlib.util
import pathlib
import sys

path = pathlib.Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
assert pathlib.Path(native.__file__).resolve() == path
dsp = native.DspConfig(1024, 512, native.WindowType.HANN, native.DetectorType.SAMPLE,
    native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64, 1, 1,
    8.6, native.CalibrationStatus.UNCALIBRATED, "", native.CONTRACT_SCHEMA_VERSION)
def channel(name):
    source = native.SourceDescriptor(native.SourceType.LIVE_IQ, name, name,
        "mock:paired", "", "paired-test", native.CONTRACT_SCHEMA_VERSION, {})
    return native.DualRxChannelDspConfig(source, dsp)
left, right = channel("exact-rx1"), channel("exact-rx2")
legacy = native.DualRxDspConfig(left, right, False, 4)
assert legacy.max_input_samples_per_push == 262144
assert legacy.backend.preference == native.ComputeBackendKind.CPU
budget = native.dual_rx_dsp_resource_budget(legacy)
assert budget.analytical_output_capacity == 515
assert budget.input_payload_bytes == 4194304
assert budget.dsp_working_bytes == 335872
assert budget.spectrum_backlog_bytes == 17039360
assert budget.total_bytes == 21569536
for obj, field, changed in ((legacy, "max_input_samples_per_push", 1),
        (budget, "analytical_output_capacity", 4)):
    try:
        setattr(obj, field, changed)
    except AttributeError:
        pass
    else:
        raise AssertionError("paired admission readback must be immutable")
publisher = native.DualRxDspPublisher()
publisher.configure(legacy)
assert not hasattr(publisher, "push") and not hasattr(publisher, "push_iq")
assert publisher.poll_spectrum_frames(0) == []
assert publisher.metrics.resource_budget.analytical_output_capacity == 515
auto = native.DualRxDspConfig(left, right, max_input_samples_per_push=4096,
    backend=native.DspBackendSelectionOptions(native.ComputeBackendKind.AUTO))
publisher.configure(auto)
metrics = publisher.metrics
assert metrics.primary.requested_preference == metrics.secondary.requested_preference == native.ComputeBackendKind.AUTO
assert metrics.primary.active_backend == metrics.secondary.active_backend == native.ComputeBackendKind.CPU
assert metrics.primary.backend_fallback_count == metrics.secondary.backend_fallback_count == 0
for maximum in (0, 8388609, 4294967295):
    try:
        native.DualRxDspConfig(left, right, max_input_samples_per_push=maximum)
    except native.ConfigurationError:
        pass
    else:
        raise AssertionError("combined input memory must refuse before any acquisition")
forced = native.DualRxDspConfig(left, right,
    backend=native.DspBackendSelectionOptions(native.ComputeBackendKind.HIP))
try:
    publisher.configure(forced)
except native.BackendUnavailableError:
    pass
else:
    raise AssertionError("forced unavailable backend cannot silently become CPU")
assert publisher.metrics.primary.requested_preference == native.ComputeBackendKind.AUTO
publisher.mark_shared_gap()
assert publisher.metrics.shared_input_gaps == 1 and publisher.metrics.paired_frames_abandoned == 0
publisher.reset()
assert publisher.metrics.shared_input_gaps == 1
print("paired native budget/factory/readonly/legacy/no-raw binding PASS; no physical dual RX")
'''
        result = subprocess.run(
            [sys.executable, "-c", code, MODULE], cwd=ROOT,
            text=True, capture_output=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
