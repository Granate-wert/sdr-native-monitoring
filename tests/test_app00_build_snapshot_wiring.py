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
