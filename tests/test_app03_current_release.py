"""Windows promotion transaction with synthetic packages; never launch an EXE."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "publish_sdr_current.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")


def quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@unittest.skipUnless(SHELL, "PowerShell is required")
class CurrentReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sdr-promotion-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "dist/SDRNativeMonitoring-CPU-test/SDRNativeMonitoring"
        self.current = self.root / "dist/SDRNativeMonitoring-CPU/SDRNativeMonitoring"
        self.package(self.source, b"synthetic new package, not executable")
        self.package(self.current, b"synthetic old package, not executable")

    def package(self, path, payload):
        path.mkdir(parents=True)
        (path / "SDRNativeMonitoring.exe").write_bytes(payload)
        manifest = dict(schema="sdr-native-release-manifest", schema_version=1,
                        product="SDR Native Monitoring", lane="CPU", python_abi="cp313",
                        version="test", files=[dict(path="SDRNativeMonitoring.exe", bytes=len(payload),
                                                   sha256=hashlib.sha256(payload).hexdigest())])
        (path / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def run_promotion(self, promote=True, setup="", source=None, target_tag=None):
        target = "" if target_tag is None else f"-TargetTag '{target_tag}' "
        command = (f". {quoted(SCRIPT)}; {setup}; Publish-SdrCurrent -RepositoryRoot {quoted(self.root)} "
                   f"-SourcePackage {quoted(source or self.source)} -Lane CPU "
                   + target + ("-Promote " if promote else "") + "| ConvertTo-Json")
        return subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True, timeout=30)

    def assert_old_unchanged(self):
        self.assertEqual((self.current / "SDRNativeMonitoring.exe").read_bytes(),
                         b"synthetic old package, not executable")

    def test_plan_is_read_only(self):
        result = self.run_promotion(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["promoted"])
        self.assert_old_unchanged()
        self.assertFalse((self.root / "dist/archive").exists())
        self.assertFalse(list((self.root / "dist").glob(".sdr-current*")))

    def test_cli_default_and_tagged_plans_match_library_destinations(self):
        script = self.root / SCRIPT.name
        shutil.copyfile(SCRIPT, script)
        for tag in (None, "APP04-fixed"):
            with self.subTest(tag=tag):
                command = [SHELL, "-NoProfile", "-NonInteractive", "-File", str(script),
                           "-SourcePackage", str(self.source), "-Lane", "CPU"]
                if tag is not None:
                    command += ["-TargetTag", tag]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                observed = json.loads(result.stdout)
                expected = (self.current if tag is None else
                            self.root / f"dist/SDRNativeMonitoring-CPU-{tag}/SDRNativeMonitoring")
                self.assertEqual(Path(observed["current"]), expected)
                self.assertFalse(observed["promoted"])
                self.assert_old_unchanged()
        self.assertFalse((self.root / "dist/archive").exists())

    def test_fixed_test_path_swap_leaves_stable_untouched(self):
        target = self.root / "dist/SDRNativeMonitoring-CPU-APP04-fixed/SDRNativeMonitoring"
        self.package(target, b"old test build")
        result = self.run_promotion(target_tag="APP04-fixed")
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(Path(observed["current"]), target)
        self.assertEqual(observed["target_tag"], "APP04-fixed")
        self.assertTrue(observed["promoted"])
        self.assert_old_unchanged()
        self.assertEqual((target / "SDRNativeMonitoring.exe").read_bytes(),
                         (self.source / "SDRNativeMonitoring.exe").read_bytes())
        self.assertEqual((Path(observed["archive"]) / "SDRNativeMonitoring.exe").read_bytes(),
                         b"old test build")

    def test_test_path_plan_is_read_only(self):
        result = self.run_promotion(False, target_tag="APP04-fixed")
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertFalse(observed["promoted"])
        self.assertFalse(Path(observed["current"]).exists())
        self.assertFalse((self.root / "dist/archive").exists())
        self.assert_old_unchanged()

    def test_same_source_target_and_unsafe_target_tags_are_rejected(self):
        for tag in ("test", "TEST", "../escape", "a/b", "a:b", "x" * 65):
            with self.subTest(tag=tag):
                result = self.run_promotion(target_tag=tag)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assert_old_unchanged()
                self.assertFalse((self.root / "dist/archive").exists())
                self.assertEqual((self.source / "SDRNativeMonitoring.exe").read_bytes(),
                                 b"synthetic new package, not executable")

    def test_failed_test_path_verification_restores_only_test_target(self):
        target = self.root / "dist/SDRNativeMonitoring-CPU-APP04-fixed/SDRNativeMonitoring"
        self.package(target, b"old test build")
        setup = f"""function Move-Item {{
            param($LiteralPath, $Destination)
            Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
            if ([IO.Path]::GetFileName($LiteralPath) -like '.sdr-current-stage-*') {{
                [IO.File]::WriteAllText((Join-Path {quoted(target)} 'extra.txt'), 'synthetic corruption')
            }}
        }}"""
        result = self.run_promotion(setup=setup, target_tag="APP04-fixed")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_unchanged()
        self.assertEqual((target / "SDRNativeMonitoring.exe").read_bytes(), b"old test build")
        stages = list((self.root / "dist").glob(".sdr-current-stage-*"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / "extra.txt").exists())

    def test_running_test_target_is_the_guarded_directory(self):
        target = self.root / "dist/SDRNativeMonitoring-CPU-APP04-fixed/SDRNativeMonitoring"
        self.package(target, b"old test build")
        setup = f"""function Assert-SdrCurrentStopped {{
            param([string]$Current)
            if ($Current -eq {quoted(target)}) {{ throw 'synthetic running test target' }}
        }}"""
        result = self.run_promotion(setup=setup, target_tag="APP04-fixed")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_unchanged()
        self.assertEqual((target / "SDRNativeMonitoring.exe").read_bytes(), b"old test build")

    def test_complete_swap_preserves_archive_and_tagged_source(self):
        result = self.run_promotion()
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertTrue(observed["promoted"])
        self.assertEqual((self.current / "SDRNativeMonitoring.exe").read_bytes(),
                         (self.source / "SDRNativeMonitoring.exe").read_bytes())
        self.assertEqual((Path(observed["archive"]) / "SDRNativeMonitoring.exe").read_bytes(),
                         b"synthetic old package, not executable")

    def test_corrupt_or_unmanifested_input_never_changes_current(self):
        for added in (False, True):
            with self.subTest(added=added):
                if added:
                    source = self.root / "dist/SDRNativeMonitoring-CPU-other/SDRNativeMonitoring"
                    self.package(source, b"valid")
                    (source / "extra.txt").write_text("not manifested")
                else:
                    source = self.source
                    (source / "SDRNativeMonitoring.exe").write_bytes(b"corrupted")
                result = self.run_promotion(source=source)
                self.assertNotEqual(result.returncode, 0)
                self.assert_old_unchanged()

    def test_outside_or_untagged_source_is_rejected(self):
        for source in (self.current, self.root / "outside/SDRNativeMonitoring"):
            if source != self.current:
                self.package(source, b"outside")
            result = self.run_promotion(source=source)
            self.assertNotEqual(result.returncode, 0)
            self.assert_old_unchanged()

    def test_running_target_is_rejected(self):
        result = self.run_promotion(setup="function Assert-SdrCurrentStopped { throw 'synthetic running target' }")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_unchanged()

    def test_failed_install_rename_restores_previous_directory(self):
        setup = """function Move-Item {
            param($LiteralPath, $Destination)
            if ([IO.Path]::GetFileName($LiteralPath) -like '.sdr-current-stage-*') {
                throw 'synthetic install rename failure'
            }
            Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
        }"""
        result = self.run_promotion(setup=setup)
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_unchanged()
        self.assertEqual(len(list((self.root / "dist").glob(".sdr-current-stage-*"))), 1)

    def test_lane_mismatch_and_manifest_traversal_are_rejected(self):
        manifest_path = self.source / "release_manifest.json"
        original = json.loads(manifest_path.read_text())
        for change in (dict(lane="CUDA"), dict(files=[dict(path="../escape", bytes=0, sha256="x")])):
            manifest_path.write_text(json.dumps(original | change))
            result = self.run_promotion()
            self.assertNotEqual(result.returncode, 0)
            self.assert_old_unchanged()

    def test_windows_alias_cannot_hide_unmanifested_file(self):
        manifest_path = self.source / "release_manifest.json"
        original = json.loads(manifest_path.read_text())
        (self.source / "unmanifested.dll").write_bytes(b"unmanifested")
        for suffix in (".", " "):
            manifest = original | {"files": original["files"] + [
                original["files"][0] | {"path": "SDRNativeMonitoring.exe" + suffix}]}
            manifest_path.write_text(json.dumps(manifest))
            result = self.run_promotion()
            self.assertNotEqual(result.returncode, 0)
            self.assert_old_unchanged()

    def test_final_verification_failure_rolls_back_and_retains_rejected_bytes(self):
        setup = f"""function Move-Item {{
            param($LiteralPath, $Destination)
            Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
            if ([IO.Path]::GetFileName($LiteralPath) -like '.sdr-current-stage-*') {{
                [IO.File]::WriteAllText((Join-Path {quoted(self.current)} 'extra.txt'), 'synthetic corruption')
            }}
        }}"""
        result = self.run_promotion(setup=setup)
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_unchanged()
        stages = list((self.root / "dist").glob(".sdr-current-stage-*"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / "extra.txt").exists())
