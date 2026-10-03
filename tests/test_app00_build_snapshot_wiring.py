"""Protect capture/verification ordering of the full source-bound pipeline."""
from pathlib import Path
import unittest


class BuildSnapshotWiringTests(unittest.TestCase):
    def test_full_pipeline_captures_before_native_and_checks_after_freeze(self):
        source = (Path(__file__).resolve().parents[1] / "build_sdr_release.ps1").read_text(encoding="utf-8")
        capture = source.index('--output $sourceSnapshotPath')
        native = source.index('& (Join-Path $repoRoot "build_native_sdr.ps1")')
        first_verify = source.index('--verify $sourceSnapshotPath')
        freeze = source.index('& $python -m PyInstaller')
        last_verify = source.rindex('--verify $sourceSnapshotPath')
        package_provenance = source.index("'build_provenance.json'")
        manifest = source.index('& $python $preflight --dist-dir')
        self.assertLess(capture, native)
        self.assertLess(native, first_verify)
        self.assertLess(first_verify, freeze)
        self.assertLess(freeze, last_verify)
        self.assertLess(last_verify, package_provenance)
        self.assertLess(package_provenance, manifest)
        self.assertIn('$bindSource = -not ($SkipNative -or $SkipFreeze -or $SkipTests)', source)
        self.assertIn('pipeline-bound-not-binary-attested', source)
        self.assertIn('packaged native artifact differs from the native build output', source)

    def test_exact_freezer_receipt_is_captured_before_outputs_and_bound_to_manifest(self):
        source = (Path(__file__).resolve().parents[1] / "build_sdr_release.ps1").read_text(encoding="utf-8")
        preflight = source.index('$freezerOutput = & $python')
        refusal = source.index('if ($LASTEXITCODE -ne 0) { throw "PyInstaller console policy preflight failed" }')
        decode = source.index('$freezerReport = $freezerOutput | ConvertFrom-Json')
        create = source.index('New-Item -ItemType Directory -Force -Path $releaseRoot')
        native = source.index('& (Join-Path $repoRoot "build_native_sdr.ps1")')
        receipt = source.index('freezer_preflight = $freezerReport')
        manifest = source.index('& $python $preflight --dist-dir')
        self.assertLess(preflight, refusal)
        self.assertLess(refusal, decode)
        self.assertLess(decode, create)
        self.assertLess(create, native)
        self.assertLess(native, receipt)
        self.assertLess(receipt, manifest)
        self.assertIn('$provenance | ConvertTo-Json -Depth 6', source)
        self.assertIn('pipeline-bound-not-binary-attested', source)
