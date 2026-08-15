"""R11-E preflight, route-redaction and finite-attempt tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sdr_monitor.benchmarks.r11e_hackrf_capability_observation import (
    R11E_EVIDENCE_SCHEMA,
    build_r11e_preflight,
    execute_r11e_observation,
    validate_r11e_evidence,
    validate_r11e_preflight,
    write_new_json,
)
from sdr_monitor.services.hackrf_capability_adapter import (
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfReadOnlyProbe,
)


_DIGEST = "a" * 64
_REVISION = "b" * 64


class _Port:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def probe(self) -> HackrfReadOnlyProbe:
        self.calls.append("probe")
        if self.fail:
            raise RuntimeError("USB instance secret")
        return HackrfReadOnlyProbe(
            HackrfBoardKind.HACKRF_ONE,
            (0, 0, 1, 2),
            "2024.02.1",
            0x0107,
        )

    def close(self) -> None:
        self.calls.append("close")


class R11EHackrfCapabilityObservationTests(unittest.TestCase):
    def test_preflight_is_exact_route_free_and_has_symbol_allowlist(self) -> None:
        preflight = build_r11e_preflight(_DIGEST, _REVISION)
        validate_r11e_preflight(preflight, _DIGEST, _REVISION)
        encoded = json.dumps(preflight, sort_keys=True).casefold()
        for marker in ("usb:", "vid_", "pid_", "start_rx", "set_freq"):
            self.assertNotIn(marker, encoded)
        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            validate_r11e_preflight(preflight, "c" * 64, _REVISION)

    def test_success_is_redacted_and_one_attempt(self) -> None:
        port = _Port()
        evidence = execute_r11e_observation(
            build_r11e_preflight(_DIGEST, _REVISION),
            HackrfCapabilityAdapter(lambda: port),
            runtime_digest=_DIGEST,
            source_revision=_REVISION,
        )
        self.assertEqual(evidence["schema"], R11E_EVIDENCE_SCHEMA)
        self.assertEqual(evidence["status"], "OBSERVED")
        self.assertEqual(port.calls, ["probe", "close"])
        validate_r11e_evidence(evidence)
        encoded = json.dumps(evidence)
        self.assertNotIn("2024.02.1", encoded)
        self.assertNotIn("USB", encoded)

    def test_failure_is_not_retried_or_echoed(self) -> None:
        port = _Port(fail=True)
        evidence = execute_r11e_observation(
            build_r11e_preflight(_DIGEST, _REVISION),
            HackrfCapabilityAdapter(lambda: port),
            runtime_digest=_DIGEST,
            source_revision=_REVISION,
        )
        self.assertEqual(evidence["status"], "NOT_OBSERVED")
        self.assertEqual(port.calls, ["probe", "close"])
        self.assertNotIn("secret", json.dumps(evidence).casefold())
        validate_r11e_evidence(evidence)

    def test_execution_rejects_digest_mismatch_before_port_factory(self) -> None:
        port = _Port()
        with self.assertRaisesRegex(ValueError, "does not exactly match"):
            execute_r11e_observation(
                build_r11e_preflight(_DIGEST, _REVISION),
                HackrfCapabilityAdapter(lambda: port),
                runtime_digest="c" * 64,
                source_revision=_REVISION,
            )
        self.assertEqual(port.calls, [])

    def test_output_is_atomic_and_write_once(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "preflight.json"
            payload = build_r11e_preflight(_DIGEST, _REVISION)
            write_new_json(output, payload)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)
            with self.assertRaises(FileExistsError):
                write_new_json(output, payload)


if __name__ == "__main__":
    unittest.main()
