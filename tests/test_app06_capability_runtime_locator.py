"""Static manifest locator tests: no DLL loading or in-memory attestation."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sdr_monitor.services.source_capability_providers import _qualified_hackrf_sdk_directory


class CapabilityRuntimeLocatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.module = self.folder / "_sdr_native.cp313-win_amd64.pyd"
        self.module.write_bytes(b"fixture-not-an-executable")
        self.hashes = {}
        for name in ("hackrf.dll", "libusb-1.0.dll", "pthreadVC3.dll"):
            payload = f"fixture-{name}".encode()
            (self.folder / name).write_bytes(payload)
            self.hashes[name] = hashlib.sha256(payload).hexdigest()
        self.manifest = {"hackrf_official_compiled": True, "hackrf_factory_contract_version": 2,
                         "artifact_sha256": hashlib.sha256(self.module.read_bytes()).hexdigest(),
                         "hackrf_runtime_sha256": self.hashes}
        self.manifest_path = self.folder / "native_build_manifest.json"
        self._write_manifest()
        self.factory = Mock(side_effect=AssertionError("No native/SDK calls in locator"))
        self.native = SimpleNamespace(__file__=str(self.module), HACKRF_FACTORY_CONTRACT_VERSION=2,
                                      create_hackrf_runtime_dsp_control=self.factory)

    def _write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_only_complete_exact_sibling_set_and_already_loaded_factory2_qualifies(self):
        self.assertEqual(_qualified_hackrf_sdk_directory(self.native), self.folder)
        self.factory.assert_not_called()

    def test_missing_or_changed_runtime_and_module_refuses(self):
        for name in (*self.hashes, self.module.name):
            target = self.folder / name
            content = target.read_bytes()
            with self.subTest(name=name, problem="missing"):
                target.unlink()
                self.assertIsNone(_qualified_hackrf_sdk_directory(self.native))
            target.write_bytes(content + b"tampered")
            with self.subTest(name=name, problem="tampered"):
                self.assertIsNone(_qualified_hackrf_sdk_directory(self.native))
            target.write_bytes(content)
        self.factory.assert_not_called()

    def test_missing_extra_bad_or_oversized_manifest_refuses(self):
        for value in (None, [], {**self.manifest, "hackrf_official_compiled": False},
                      {**self.manifest, "hackrf_factory_contract_version": True},
                      {**self.manifest, "hackrf_factory_contract_version": 1},
                      {**self.manifest, "hackrf_runtime_sha256": {**self.hashes, "extra.dll": "unknown"}},
                      {**self.manifest, "hackrf_runtime_sha256": {"hackrf.dll": self.hashes["hackrf.dll"]}}):
            with self.subTest(value=value):
                self.manifest_path.write_text(json.dumps(value), encoding="utf-8")
                self.assertIsNone(_qualified_hackrf_sdk_directory(self.native))
        for payload in (b"not JSON", b"x" * 16_385):
            self.manifest_path.write_bytes(payload)
            self.assertIsNone(_qualified_hackrf_sdk_directory(self.native))
        self.manifest_path.unlink()
        self.assertIsNone(_qualified_hackrf_sdk_directory(self.native))
        self.factory.assert_not_called()

    def test_off_old_bool_or_missing_native_surface_refuses_before_filesystem(self):
        for native in (object(), SimpleNamespace(__file__=str(self.module)),
                       SimpleNamespace(__file__=str(self.module), HACKRF_FACTORY_CONTRACT_VERSION=True,
                                       create_hackrf_runtime_dsp_control=self.factory),
                       SimpleNamespace(__file__=str(self.module), HACKRF_FACTORY_CONTRACT_VERSION=1,
                                       create_hackrf_runtime_dsp_control=self.factory),
                       SimpleNamespace(__file__=None, HACKRF_FACTORY_CONTRACT_VERSION=2,
                                       create_hackrf_runtime_dsp_control=self.factory)):
            with self.subTest(native=native):
                self.assertIsNone(_qualified_hackrf_sdk_directory(native))
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
