"""Build policy guards; actual Ninja header invalidation is a separate native gate."""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeDependencyPolicyTests(unittest.TestCase):
    def test_windows_presets_prefer_stable_compiler_language(self):
        presets = json.loads((ROOT / "native/sdr_core/CMakePresets.json").read_text(encoding="utf-8"))
        cpu = next(p for p in presets["configurePresets"] if p["name"] == "windows-msvc-cpu")
        self.assertEqual(cpu["environment"]["VSLANG"], "1033")

    def test_actual_prefix_overrides_potentially_misdecoded_cache(self):
        source = (ROOT / "native/sdr_core/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('set(CMAKE_CL_SHOWINCLUDES_PREFIX "${SDR_MSVC_SHOWINCLUDES_PREFIX}")', source)
        wrapper = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        self.assertIn("/showIncludes /EP /TP", wrapper)
        self.assertIn("[Console]::OutputEncoding = $probeOutputEncoding", wrapper)
        self.assertIn("$dependencyPrefix.Contains([char]0xfffd)", wrapper)

    def test_dependency_guard_precedes_activation_and_recovers_old_objects(self):
        wrapper = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")
        self.assertIn("$buildArguments += '--clean-first'", wrapper)
        guard = wrapper.index("Native header dependency verification failed")
        self.assertLess(guard, wrapper.index('$sourceCommit ='))
        self.assertLess(guard, wrapper.index('Copy-Item -LiteralPath $artifacts[0].FullName'))


if __name__ == "__main__":
    unittest.main()
