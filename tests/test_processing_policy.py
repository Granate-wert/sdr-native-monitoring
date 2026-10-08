"""Pure DCSP118 contracts; no imports of SDK/native/Qt or physical RX tests."""

import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from sdr_monitor.domain.processing_policy import (
    AppliedProcessingContextV1, DC_REMOVED_MASK, HostDcMode, HostSpurMode, LegacyProcessingObservation,
    MAX_POLICY_BYTES, MAX_VALIDITY_ZONES, NativeProcessingSupport, ProcessingFrameKey,
    ProcessingZone, SdrProcessingPolicyV1, SpurProfileReference, ValidityReason,
    policy_from_saved_settings, validate_processing_receipt, validate_processing_support,
)
from sdr_monitor.domain.receiver_topology import ReceiverChain


def digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def key() -> ProcessingFrameKey:
    return ProcessingFrameKey("resource", "producer", ReceiverChain.RX1, 4, 5, digest("grid"),
                              "dBFS/bin", "power-norm-v1", "cpu", 100e6, 61.44e6, 56e6)


def profile() -> SpurProfileReference:
    return SpurProfileReference("профиль-A", 2, digest("profile"), digest("applicability"))


def receipt(policy: SdrProcessingPolicyV1) -> AppliedProcessingContextV1:
    processed = policy.dc_mode is not HostDcMode.OFF or policy.spur_mode is HostSpurMode.PROFILE_NOTCH
    zones = (ProcessingZone(99.9e6, 100.1e6, ValidityReason.MEASUREMENT_LIMITED),) if processed else ()
    flags = (1 << 31) | 1  # Existing UNCALIBRATED schema5 bit.
    if policy.dc_mode is HostDcMode.BLOCK_MEAN:
        flags |= DC_REMOVED_MASK
    return AppliedProcessingContextV1(policy.digest, 7, key(), policy.dc_mode, policy.spur_mode,
                                      policy.spur_profile.digest if policy.spur_profile else None,
                                      processed, zones, flags)


class ProcessingPolicyTests(unittest.TestCase):
    def test_dc_bit_matches_existing_python_and_cpp_contract_without_native_import(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "esw_dfl/sdr/contracts.py").read_text(encoding="utf-8-sig"))
        quality = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "QualityFlag")
        flag = next(node.value for node in quality.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "DC_REMOVED" for target in node.targets))
        self.assertIsInstance(flag, ast.BinOp)
        self.assertIsInstance(flag.op, ast.LShift)
        self.assertEqual((flag.left.value, flag.right.value), (1, 9))
        self.assertEqual(DC_REMOVED_MASK, 512)
        cpp = (root / "native/sdr_core/include/sdr_core/types.hpp").read_text(encoding="utf-8-sig")
        self.assertIn("DcRemoved = 1U << 9U", cpp)

    def test_default_off_golden_bytes(self):
        expected = (b'{"compare_raw":false,"dc_mode":"off","schema":"sdr-processing-policy",'
                    b'"schema_version":1,"spur_mode":"off","spur_profile":null}')
        policy = SdrProcessingPolicyV1()
        self.assertEqual(policy.canonical_bytes(), expected)
        self.assertEqual(policy.digest, digest(expected.decode()))
        self.assertEqual(policy.digest, "sha256:ea4bbcf4d10b2def528a74327c67e3799144dc403b68f4caccb477e181e16d42")
        self.assertTrue(policy.is_off)
        self.assertEqual(policy_from_saved_settings(None), policy)
        self.assertEqual(policy_from_saved_settings(expected), policy)

    def test_all_request_modes_round_trip_and_schema(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/"
                             "sdr_processing_policy_v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        for dc in HostDcMode:
            for spur in HostSpurMode:
                for compare in (False, True):
                    with self.subTest(dc=dc, spur=spur, compare=compare):
                        policy = SdrProcessingPolicyV1(dc, spur, None if spur is HostSpurMode.OFF else profile(), compare)
                        payload = policy.canonical_bytes()
                        Draft202012Validator(schema).validate(json.loads(payload))
                        self.assertEqual(SdrProcessingPolicyV1.from_json(payload), policy)
                        reversed_fields = json.dumps(dict(reversed(list(json.loads(payload).items())))).encode()
                        self.assertEqual(SdrProcessingPolicyV1.from_json(reversed_fields).digest, policy.digest)

    def test_recipe_digest_changes_but_not_for_frame_epoch(self):
        policy = SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN)
        self.assertNotEqual(policy.digest, SdrProcessingPolicyV1().digest)
        self.assertNotEqual(policy.digest, replace(policy, compare_raw=True).digest)
        p = SdrProcessingPolicyV1(spur_mode=HostSpurMode.CANDIDATES, spur_profile=profile())
        self.assertNotEqual(p.digest, replace(p, spur_profile=replace(profile(), revision=3)).digest)
        self.assertEqual(receipt(policy).policy_digest, replace(receipt(policy), frame_key=replace(key(),
                         acquisition_epoch=6)).policy_digest)

    def test_schema_refuses_structurally_invalid_requests(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/"
                             "sdr_processing_policy_v1.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        base = json.loads(SdrProcessingPolicyV1().canonical_bytes())
        for patch in ({"schema_version": True}, {"schema_version": 2}, {"compare_raw": 0},
                      {"spur_mode": "mystery"}, {"dc_mode": "BLOCK_MEAN"}, {"extra": False},
                      {"spur_mode": "profile_notch_v1"}, {"spur_profile": {}}):
            with self.subTest(patch=patch):
                self.assertFalse(validator.is_valid({**base, **patch}))
        for name in base:
            with self.subTest(missing=name):
                self.assertFalse(validator.is_valid({k: v for k, v in base.items() if k != name}))

    def test_unknown_fields_versions_algorithms_and_strict_json_types(self):
        base = json.loads(SdrProcessingPolicyV1().canonical_bytes())
        cases = [("schema", "wrong"), ("schema_version", True), ("schema_version", 1.0),
                 ("schema_version", 2), ("schema_version", None), ("dc_mode", "BLOCK_MEAN"),
                 ("dc_mode", False), ("spur_mode", "mystery"), ("compare_raw", 0), ("extra", 1)]
        for name, invalid in cases:
            with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                SdrProcessingPolicyV1.from_json(json.dumps({**base, name: invalid}).encode())
        for payload in (b"[]", b"null", b"{}", b"", b"\xff", b"[" * 1100,
                        b" " * (MAX_POLICY_BYTES + 1), SdrProcessingPolicyV1().canonical_bytes()[:-1],
                        SdrProcessingPolicyV1().canonical_bytes().replace(b'"schema_version":1',
                                                                       b'"schema_version":NaN')):
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                SdrProcessingPolicyV1.from_json(payload)
        with self.assertRaises(ValueError):
            SdrProcessingPolicyV1.from_json(bytearray(SdrProcessingPolicyV1().canonical_bytes()))

    def test_duplicate_keys_rejected_at_both_levels(self):
        raw = SdrProcessingPolicyV1().canonical_bytes()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            SdrProcessingPolicyV1.from_json(raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'))
        p = SdrProcessingPolicyV1(spur_mode=HostSpurMode.CANDIDATES, spur_profile=profile()).canonical_bytes()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            SdrProcessingPolicyV1.from_json(p.replace(b'"revision":2', b'"revision":2,"revision":2'))

    def test_profile_wire_does_not_accept_unknown_or_coerced_fields(self):
        base = json.loads(SdrProcessingPolicyV1(spur_mode=HostSpurMode.CANDIDATES,
                                               spur_profile=profile()).canonical_bytes())
        for name, invalid in (("revision", True), ("revision", 2.0), ("revision", 0),
                              ("digest", "unknown"), ("profile_id", "\ud800"), ("extra", 1)):
            with self.subTest(name=name, invalid=repr(invalid)), self.assertRaises(ValueError):
                SdrProcessingPolicyV1.from_json(json.dumps({**base, "spur_profile": {
                    **base["spur_profile"], name: invalid}}).encode())
        malformed = {**base, "spur_profile": {"profile_id": "only-name"}}
        with self.assertRaises(ValueError):
            SdrProcessingPolicyV1.from_json(json.dumps(malformed).encode())

    def test_profiles_are_bounded_typed_immutable_and_not_guessed(self):
        for invalid in ("", " space", "space ", "x" * 129, "nul\0", "\ud800"):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ValueError):
                replace(profile(), profile_id=invalid)
        for invalid in (0, -1, True, 2.0, 1 << 64):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                replace(profile(), revision=invalid)
        with self.assertRaises(ValueError):
            replace(profile(), digest=profile().digest.upper())
        for kwargs in ({"spur_profile": profile()}, {"spur_mode": HostSpurMode.CANDIDATES},
                       {"dc_mode": "off"}, {"compare_raw": 1}, {"spur_profile": {}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SdrProcessingPolicyV1(**kwargs)
        with self.assertRaises(FrozenInstanceError):
            profile().revision = 8

    def test_legacy_off_and_unsupported_refusal_are_separate_from_application(self):
        validate_processing_support(SdrProcessingPolicyV1(), NativeProcessingSupport())
        validate_processing_support(SdrProcessingPolicyV1(), NativeProcessingSupport(2))
        for policy in (SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN),
                       SdrProcessingPolicyV1(compare_raw=True),
                       SdrProcessingPolicyV1(spur_mode=HostSpurMode.PROFILE_NOTCH, spur_profile=profile())):
            for support in (NativeProcessingSupport(), NativeProcessingSupport(2), NativeProcessingSupport(1)):
                with self.subTest(policy=policy, support=support), self.assertRaises(ValueError):
                    validate_processing_support(policy, support)
        validate_processing_support(SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN),
                                    NativeProcessingSupport(1, frozenset({HostDcMode.BLOCK_MEAN})))
        with self.assertRaises(ValueError):
            NativeProcessingSupport(1, {HostDcMode.BLOCK_MEAN})
        with self.assertRaises(ValueError):
            NativeProcessingSupport(True)

    def test_exact_frame_policy_revision_join_refuses_every_changed_axis(self):
        policy = SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN)
        current = receipt(policy)
        validate_processing_receipt(policy, key(), 7, current)
        changes = {"resource_id": "other", "source_id": "other", "receiver": ReceiverChain.RX2,
                   "config_generation": 5, "acquisition_epoch": 6, "grid_digest": digest("other"),
                   "unit": "dBFS/Hz", "normalization_version": "other", "backend_id": "other",
                   "actual_lo_hz": 101e6, "sample_rate_hz": 30.72e6, "analog_bandwidth_hz": 36e6}
        for name, value in changes.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_processing_receipt(policy, replace(key(), **{name: value}), 7, current)
        with self.assertRaises(ValueError):
            validate_processing_receipt(policy, key(), 8, current)
        with self.assertRaises(ValueError):
            validate_processing_receipt(SdrProcessingPolicyV1(), key(), 7, current)
        with self.assertRaises(ValueError):
            validate_processing_receipt(policy, key(), True, current)
        compare = replace(policy, compare_raw=True)
        with self.assertRaises(ValueError):
            validate_processing_receipt(compare, key(), 7, replace(current, policy_digest=compare.digest))
        validate_processing_receipt(compare, key(), 7,
                                    replace(current, policy_digest=compare.digest, comparison_applied=True))

    def test_profile_receipt_requires_same_recipe_and_affected_intervals(self):
        policy = SdrProcessingPolicyV1(spur_mode=HostSpurMode.PROFILE_NOTCH, spur_profile=profile())
        current = receipt(policy)
        validate_processing_receipt(policy, key(), 7, current)
        with self.assertRaises(ValueError):
            validate_processing_receipt(policy, key(), 7, replace(current, profile_digest=digest("other")))
        with self.assertRaises(ValueError):
            replace(current, profile_digest=None)
        candidate = receipt(replace(policy, spur_mode=HostSpurMode.CANDIDATES))
        self.assertFalse(candidate.whole_frame_modified)
        self.assertEqual(candidate.zones, ())
        with self.assertRaises(ValueError):
            replace(candidate, whole_frame_modified=True)

    def test_coordinate_bounds_and_flags_are_not_cleaned_by_filter(self):
        for field in ("actual_lo_hz", "sample_rate_hz", "analog_bandwidth_hz"):
            for value in (0.0, -1.0, float("nan"), float("inf"), True, 1):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    replace(key(), **{field: value})
        for field, value in (("resource_id", ""), ("receiver", "rx1"), ("unit", "dBm/bin"),
                             ("grid_digest", "unknown"), ("acquisition_epoch", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(key(), **{field: value})
        current = receipt(SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN))
        self.assertTrue(current.native_quality_flags & (1 << 31))
        self.assertTrue(current.native_quality_flags & 1)
        self.assertIsNone(current.hardware_dc_tracking)
        for field, value in (("native_quality_flags", 0), ("native_quality_flags", 1 << 32),
                             ("whole_frame_modified", False), ("hardware_dc_tracking", 1)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(current, **{field: value})

    def test_validity_bounds_and_mutability(self):
        current = receipt(SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN))
        zone = current.zones[0]
        replace(current, zones=(zone,) * MAX_VALIDITY_ZONES)
        for zones in ([zone], (zone,) * (MAX_VALIDITY_ZONES + 1), ("unknown",)):
            with self.subTest(zones_type=type(zones)), self.assertRaises(ValueError):
                replace(current, zones=zones)
        for values in ((0.0, 0.0), (-1.0, 2.0), (0.0, float("inf")), (True, 1.0)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ProcessingZone(*values, ValidityReason.EXCLUDED)
        with self.assertRaises(FrozenInstanceError):
            current.processing_revision = 8
        with self.assertRaises(FrozenInstanceError):
            zone.stop_hz = 200e6
        with self.assertRaises(ValueError):
            replace(receipt(SdrProcessingPolicyV1(spur_mode=HostSpurMode.PROFILE_NOTCH,
                                                spur_profile=profile())), zones=())

    def test_legacy_flags_cannot_fabricate_recipe_or_hardware_readback(self):
        self.assertIsNone(LegacyProcessingObservation(None).dc_removed_reported)
        self.assertFalse(LegacyProcessingObservation(0).dc_removed_reported)
        flags = DC_REMOVED_MASK | (1 << 31)
        observation = LegacyProcessingObservation(flags)
        self.assertTrue(observation.dc_removed_reported)
        self.assertIsNone(observation.known_recipe)
        self.assertEqual(observation.native_quality_flags, flags)
        for invalid in (-1, True, 1 << 32, 1.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                LegacyProcessingObservation(invalid)


if __name__ == "__main__":
    unittest.main()
