"""Checked TOC normalization and startup ownership, no SDK or hardware calls."""

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.freeze_sdr_official import checked_spec_source
from scripts.sdr_frozen_runtime_payload import deduplicate_shared_runtime_binaries
from sdr_monitor.frozen_shared_runtime import SHARED_COMPONENTS
from sdr_monitor.main import _native_startup_owner_state


def payload_fixture(directory):
    selected = {}
    entries = []
    for name in SHARED_COMPONENTS:
        path = directory / name
        path.write_bytes(name.encode())
        selected[name] = path
        entries.append(("sdr_monitor/" + name, str(path), "BINARY"))
    return selected, entries


class FrozenPayloadTests(unittest.TestCase):
    def test_equal_root_copies_only_omitted_unrelated_and_canonical_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            selected, entries = payload_fixture(Path(tmp))
            unrelated = ("other.dll", "not-opened", "BINARY")
            roots = [(name, str(path), "BINARY") for name, path in selected.items()]
            observed = deduplicate_shared_runtime_binaries([*roots, unrelated, *entries], selected)
            self.assertEqual(observed, [unrelated, *entries])
            self.assertTrue(all(path.read_bytes() == name.encode() for name, path in selected.items()))

    def test_different_root_bytes_refuse_instead_of_silently_selecting_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            selected, entries = payload_fixture(directory)
            other = directory / "other"
            other.mkdir()
            dll = other / "libusb-1.0.dll"
            dll.write_bytes(b"other SDK version")
            with self.assertRaisesRegex(ValueError, "incompatible bytes"):
                deduplicate_shared_runtime_binaries([*entries, (dll.name, str(dll), "BINARY")], selected)
            self.assertEqual(dll.read_bytes(), b"other SDK version")
            self.assertEqual(selected[dll.name].read_bytes(), dll.name.encode())

    def test_missing_duplicate_canonical_wrong_kind_and_incomplete_selection_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            selected, entries = payload_fixture(Path(tmp))
            for invalid in (entries[1:], [*entries, entries[0]],
                            [(entries[0][0], entries[0][1], "SYMLINK"), *entries[1:]],
                            [(Path(dest).name, src, kind) for dest, src, kind in entries]):
                with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                    deduplicate_shared_runtime_binaries(invalid, selected)
            with self.assertRaises(ValueError):
                deduplicate_shared_runtime_binaries(entries, {})

    def test_generated_spec_injection_is_before_pyz_and_retains_console_hide_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            selected, _ = payload_fixture(Path(tmp))
            source = "a = Analysis([])\npyz = PYZ(a.pure)\nexe = EXE(pyz, console=True, hide_console='hide-early')\n"
            observed = checked_spec_source(source, Path("C:/source with ' and spaces"), selected)
            ast.parse(observed)
            self.assertLess(observed.index("a.binaries = deduplicate"), observed.index("pyz = PYZ"))
            self.assertIn("console=True, hide_console='hide-early'", observed)
            for name in SHARED_COMPONENTS:
                self.assertIn(repr(name), observed)
            for invalid in (source.replace("console=True", "console=False"),
                            source.replace("hide-early", "hide-late"),
                            source + "exe2 = EXE()\n", source.replace("pyz = PYZ(a.pure)", "pyz = object()")):
                with self.subTest(source=invalid), self.assertRaises(ValueError):
                    checked_spec_source(invalid, Path(tmp), selected)

    def test_installed_generator_works_without_analysis_import_or_hardware(self):
        from PyInstaller.building import makespec
        from PyInstaller.utils.cliutils.makespec import generate_parser

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            selected, _ = payload_fixture(directory)
            options = generate_parser().parse_args(["--onedir", "--name", "PayloadPolicyTest", "--specpath", tmp,
                                                   "--hide-console", "hide-early", "main_sdr.py"])
            spec = Path(makespec.main(options.scriptname, **vars(options)))
            observed = checked_spec_source(spec.read_text(encoding="utf-8"), directory, selected)
            ast.parse(observed)
            self.assertIn("deduplicate_shared_runtime_binaries", observed)


class StartupOwnerStateTests(unittest.TestCase):
    def fixture(self):
        return SimpleNamespace(_observation_owner=SimpleNamespace(cleanup_pending=False), _native_uri=None,
                               _engine=None, _poller=None, _sweep_lease_active=False, _stream_release_failed=False,
                               _external_analyzer_owner=None)

    def test_inert_current_owners_are_false_and_missing_owner_is_not_silent_false(self):
        live = self.fixture()
        self.assertEqual(_native_startup_owner_state(live),
                         {"native_device_constructed": False, "native_engine_constructed": False})
        del live._observation_owner
        with self.assertRaises(AttributeError):
            _native_startup_owner_state(live)

    def test_retained_observation_route_or_any_stream_owner_prevents_inert_claim(self):
        for name, value in (("_native_uri", "usb:unknown"), ("_engine", object()), ("_poller", object()),
                            ("_sweep_lease_active", True), ("_stream_release_failed", True),
                            ("_external_analyzer_owner", object())):
            live = self.fixture()
            setattr(live, name, value)
            self.assertTrue(any(_native_startup_owner_state(live).values()), name)
        live = self.fixture()
        live._observation_owner.cleanup_pending = True
        self.assertTrue(_native_startup_owner_state(live)["native_device_constructed"])


if __name__ == "__main__":
    unittest.main()
