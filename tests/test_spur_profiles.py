"""Profile/diagnostic geometry only, NOT native detection or RF qualification."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest

from sdr_monitor.domain.processing_policy import HostSpurMode, SdrProcessingPolicyV1
from sdr_monitor.domain.spur_profiles import (
    MAX_SPUR_PROFILE_BYTES, SpurApplicabilityV1, SpurProfileV1, SpurProfileZoneV1,
    project_spur_profile_zones,
)
from sdr_monitor.services.spur_profiles import read_spur_profile, resolve_spur_profile


def profile():
    return SpurProfileV1("device-profile", 1, SpurApplicabilityV1(
        "ad936x", "serial-actual-observation", "rx1", "sha256:" + "a" * 64,
        "sha256:" + "b" * 64, 1_024_000, 1_000_000, 50_000_000, 200_000_000), (
            SpurProfileZoneV1("baseband-center", "baseband", -1000, 1001, "calibration_capture"),
            SpurProfileZoneV1("fixed-rf", "rf", 100_000_000, 100_004_000, "user_marked")))


class SpurProfileTests(unittest.TestCase):
    def test_canonical_roundtrip_uses_existing_policy_reference(self):
        value = profile()
        self.assertEqual(SpurProfileV1.from_json(value.canonical_bytes()), value)
        policy = SdrProcessingPolicyV1(spur_mode=HostSpurMode.CANDIDATES, spur_profile=value.reference)
        policy = SdrProcessingPolicyV1.from_json(policy.canonical_bytes())
        self.assertEqual(resolve_spur_profile(policy, value.canonical_bytes()), value)
        with self.assertRaises(FrozenInstanceError):
            value.revision = 2

    def test_actual_file_read_unicode_and_bounded_corruption(self):
        value = profile()
        policy = SdrProcessingPolicyV1(spur_mode=HostSpurMode.CANDIDATES, spur_profile=value.reference)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "профиль.json"
            path.write_bytes(value.canonical_bytes())
            self.assertEqual(read_spur_profile(path, policy), value)
            path.write_bytes(b" " * (MAX_SPUR_PROFILE_BYTES + 1))
            with self.assertRaises(ValueError):
                read_spur_profile(path, policy)

    def test_foreign_reference_content_revision_applicability_refuses(self):
        value = profile()
        policy = SdrProcessingPolicyV1(spur_mode=HostSpurMode.CANDIDATES, spur_profile=value.reference)
        for other in (replace(value, revision=2), replace(value, profile_id="foreign"),
                      replace(value, applicability=replace(value.applicability, device_identity="other")),
                      replace(value, zones=(replace(value.zones[0], evidence_kind="user_marked"),))):
            with self.subTest(other=other), self.assertRaises(ValueError):
                resolve_spur_profile(policy, other.canonical_bytes())
        with self.assertRaises(ValueError):
            resolve_spur_profile(SdrProcessingPolicyV1(), value.canonical_bytes())

    def test_wire_duplicate_unknown_nested_nonfinite_and_typed_numbers_refuse(self):
        value = profile()
        data = json.loads(value.canonical_bytes())
        invalid = [b'{"schema":1,"schema":1}', b'\xff', b'{}', b'{"x":NaN}',
                   value.canonical_bytes().replace(b'"receiver":"rx1"',
                                                   b'"receiver":"rx1","receiver":"rx2"')]
        for field, replacement in (("revision", True), ("schema_version", True),
                                   ("zones", {}), ("schema", "future"), ("extra", 1)):
            wrong = dict(data, **{field: replacement})
            invalid.append(json.dumps(wrong).encode())
        for key, replacement in (("sample_rate_hz", 1.024e6), ("device_identity", "\0"),
                                  ("firmware_signature", "unknown"), ("receiver", "RX1")):
            wrong = dict(data, applicability=dict(data["applicability"], **{key: replacement}))
            invalid.append(json.dumps(wrong).encode())
        invalid.append(b'[' * 2000 + b']' * 2000)
        for payload in invalid:
            with self.subTest(payload=payload[:60]), self.assertRaises(ValueError):
                SpurProfileV1.from_json(payload)

    def test_artifact_zone_and_escaped_wire_bounds(self):
        value = profile()
        for zones in ((), value.zones * 33, (value.zones[0], value.zones[0]), [value.zones[0]]):
            with self.subTest(zones=len(zones)), self.assertRaises(ValueError):
                replace(value, zones=zones)
        zones = tuple(replace(value.zones[0], zone_id="я" * 120 + str(index)) for index in range(64))
        with self.assertRaisesRegex(ValueError, "bounded input"):
            replace(value, zones=zones)
        with self.assertRaises(ValueError):
            replace(value, zones=(replace(value.zones[0], stop_hz=600_000),))

    def test_numeric_rf_tone_stays_rf_baseband_zone_moves_with_actual_lo(self):
        value = profile()
        first = project_spur_profile_zones(value, value.applicability, center_hz=100_000_000, fft_size=1024)
        second = project_spur_profile_zones(value, value.applicability, center_hz=100_004_000, fft_size=1024)
        self.assertEqual((first[0].begin_bin, first[0].end_bin), (511, 514))
        self.assertEqual(first[0], second[0])
        self.assertEqual((first[1].begin_bin, first[1].end_bin), (512, 516))
        self.assertEqual((second[1].begin_bin, second[1].end_bin), (508, 512))
        # No amplitude, probability, confirmed-spur flag or modified samples exist.
        self.assertFalse(hasattr(first[0], "probability"))
        self.assertFalse(hasattr(first[0], "confirmed"))

    def test_partial_unobserved_coverage_and_odd_sample_rate_exact_rationals(self):
        value = profile()
        edge = replace(value, zones=(SpurProfileZoneV1("edge", "rf", 99_487_000,
                                                     99_489_000, "user_marked"),))
        zones = project_spur_profile_zones(edge, edge.applicability, center_hz=100_000_000, fft_size=1024)
        self.assertEqual((zones[0].begin_bin, zones[0].end_bin, zones[0].complete_fft_coverage), (0, 1, False))
        self.assertEqual(project_spur_profile_zones(edge, edge.applicability,
                         center_hz=101_000_000, fft_size=1024), ())
        odd = replace(value, applicability=replace(value.applicability, sample_rate_hz=1_024_001))
        self.assertEqual(project_spur_profile_zones(odd, odd.applicability,
                         center_hz=100_000_000, fft_size=1024)[1].begin_bin, 512)

    def test_foreign_family_rx_firmware_gain_fs_filter_lo_fft_refuse(self):
        value = profile()
        for key, other in (("device_identity", "different"), ("receiver", "rx2"),
                           ("firmware_signature", "sha256:" + "c" * 64),
                           ("gain_signature", "sha256:" + "c" * 64),
                           ("sample_rate_hz", 2_048_000), ("analog_bandwidth_hz", 900_000)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                project_spur_profile_zones(value, replace(value.applicability, **{key: other}),
                                          center_hz=100_000_000, fft_size=1024)
        for center, size in ((100_000_000, True), (100_000_000, 1023), (100_000_000, 524288),
                             (201_000_000, 1024), (True, 1024)):
            with self.subTest(center=center, size=size), self.assertRaises(ValueError):
                project_spur_profile_zones(value, value.applicability, center_hz=center, fft_size=size)

    def test_sub_bin_clipping_never_claims_complete_frequency_coverage(self):
        value = profile()
        # Quantized begin=0 is NOT proof that the requested interval was all measured.
        clipped = replace(value, zones=(SpurProfileZoneV1("one-hz-outside", "rf", 99_487_999,
                                                        99_489_000, "user_marked"),))
        zone = project_spur_profile_zones(clipped, clipped.applicability,
                                         center_hz=100_000_000, fft_size=1024)[0]
        self.assertEqual((zone.begin_bin, zone.end_bin), (0, 1))
        self.assertFalse(zone.complete_fft_coverage)

    def test_profile_resolution_never_enables_native_filter_support(self):
        from types import SimpleNamespace
        from sdr_monitor.domain.live import LiveAdmissionRejected, LiveConfiguration
        from sdr_monitor.services.live_processing import admit_ad_processing
        from sdr_monitor.services.source_processing import admit_source_processing

        value = profile()
        class NoNativeEffects:
            def __getattr__(self, name):
                raise AssertionError("unsupported mode reached native lookup: " + name)
        for mode in (HostSpurMode.CANDIDATES, HostSpurMode.PROFILE_NOTCH):
            policy = SdrProcessingPolicyV1(spur_mode=mode, spur_profile=value.reference)
            self.assertEqual(resolve_spur_profile(policy, value.canonical_bytes()), value)
            for family in ("hackrf", "rtl_sdr"):
                with self.subTest(mode=mode, family=family), self.assertRaises(LiveAdmissionRejected):
                    admit_source_processing(NoNativeEffects(), family, policy)
            with self.assertRaises(LiveAdmissionRejected):
                admit_ad_processing(NoNativeEffects(), replace(LiveConfiguration(), processing_policy=policy))
            self.assertFalse(hasattr(SimpleNamespace(profile=value), "applied"))

    def test_invalid_request_refuses_before_file_open(self):
        with self.assertRaises(ValueError):
            read_spur_profile(Path("missing-profile-must-not-open.json"), SdrProcessingPolicyV1())

    def test_configuration_does_not_invent_an_analog_less_than_fs_rule(self):
        value = profile()
        broad = replace(value, applicability=replace(value.applicability, analog_bandwidth_hz=2_000_000))
        self.assertEqual(SpurProfileV1.from_json(broad.canonical_bytes()), broad)


if __name__ == "__main__":
    unittest.main()
