from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
README = ROOT / "README.md"
IGNORE = ROOT / ".gitignore"


class SdrArchitectureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.architecture_text = ARCHITECTURE.read_text(encoding="utf-8")
        cls.readme_text = README.read_text(encoding="utf-8")
        cls.ignore_text = IGNORE.read_text(encoding="utf-8")

    def test_public_architecture_documents_exist(self) -> None:
        for path in (ARCHITECTURE, README, IGNORE):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_architecture_describes_current_boundary(self) -> None:
        required = (
            "# SDR Native Monitoring Architecture",
            "## 2. Current implementation boundary",
            "## 4. Layer boundaries",
            "## 5. Native SDR core",
            "## 6. Target live data flow",
            "## 7. Ownership and concurrency",
            "## 9. Units and calibration",
            "## 11. Portability",
            "## 13. Verification strategy",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, self.architecture_text)

    def test_normative_safety_rules_are_explicit(self) -> None:
        required = (
            "Input DFL and measurement recordings are always read-only.",
            "Every queue and cache has a finite bound.",
            "Python is not called for each sample or each FFT.",
            "GUI refresh rate is independent from analytical processing rate.",
            "dBm is not emitted for live SDR data without applicable calibration.",
            "CPU remains a supported reference backend",
        )
        for rule in required:
            with self.subTest(rule=rule):
                self.assertIn(rule, self.readme_text)

    def test_forbidden_claims_are_absent(self) -> None:
        forbidden = (
            "unbounded queues are allowed",
            "silent data loss is acceptable",
            "cuda is always required",
            "uncalibrated dbm is valid",
            "dfl files may be modified",
            "live pluto backend is implemented",
        )
        combined = (self.architecture_text + self.readme_text).casefold()
        for claim in forbidden:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, combined)

    def test_expected_source_boundaries_exist(self) -> None:
        expected = (
            ROOT / "esw_dfl" / "parser.py",
            ROOT / "esw_dfl" / "spectrogram.py",
            ROOT / "esw_dfl" / "domain.py",
            ROOT / "esw_dfl" / "sdr" / "contracts.py",
            ROOT / "esw_dfl" / "sdr" / "native_api.py",
            ROOT / "native" / "sdr_core" / "CMakeLists.txt",
            ROOT / "native" / "sgram_decoder" / "Cargo.toml",
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_domain_evolution_is_backward_compatible(self) -> None:
        domain = (ROOT / "esw_dfl" / "domain.py").read_text(encoding="utf-8")
        self.assertIn("class SourceDescriptor:", domain)
        self.assertIn("source_descriptor: SourceDescriptor | None = None", domain)
        self.assertIn("source_path: Path", domain)
        self.assertNotIn("source_path: Path | None", domain)

    def test_local_docs_and_agent_instructions_are_ignored(self) -> None:
        for pattern in ("/AGENTS.md", "/AGENT.md", "/docs/", "/TZ_SDR_native_monitoring/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, self.ignore_text)

    def test_generated_and_measurement_artifacts_are_ignored(self) -> None:
        for pattern in (
            "*.dfl",
            "*.iq",
            "*.cfile",
            "native/**/build/",
            "native/**/out/",
            "native/**/target/",
            "esw_dfl/_sdr_native*.pyd",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, self.ignore_text)


if __name__ == "__main__":
    unittest.main()
