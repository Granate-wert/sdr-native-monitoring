"""R12-J source contracts for CPU/CUDA frozen artifact lane parity."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class R12JCpuFrozenArtifactTests(unittest.TestCase):
    def test_frozen_native_verifier_binds_lane_to_build_metadata(self) -> None:
        verifier = (ROOT / "scripts/verify_sdr_frozen_package.py").read_text(encoding="utf-8")

        self.assertIn('expected_cuda = lane == "CUDA"', verifier)
        self.assertIn("CUDA lane does not match package lane", verifier)
        self.assertIn('"cuda_compiled": expected_cuda', verifier)

    def test_release_script_passes_its_selected_lane_to_the_frozen_verifier(self) -> None:
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")

        self.assertIn("--lane $Lane --version $version", release)
        self.assertLess(
            release.index("verify_sdr_frozen_package.py"),
            release.index("verify_sdr_frozen_libiio_runtime.py"),
        )


if __name__ == "__main__":
    unittest.main()
