"""Offline canonical serialization checks; not runtime admission or RF proof."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from sdr_monitor.domain.calibration import (
    CalibrationProfile, CalibrationProfileError, CalibrationSignature, InstrumentCalibrationContext,
)
from sdr_monitor.domain.receiver_topology import ReceiverChain
from tests import test_calibration_receiver_scope as scoped
from tests import test_sdr_calibration as legacy


class CalibrationSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(__file__).resolve().parents[1] / 'docs' / 'schemas'
        names = ('sdr_calibration_profile', 'sdr_monitor_calibration_profile')
        schemas = [json.loads((folder / f'{name}.schema.json').read_text(encoding='utf-8')) for name in names]
        registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
        for schema in schemas:
            Draft202012Validator.check_schema(schema)
        cls.legacy = Draft202012Validator(schemas[0], registry=registry)
        cls.v2 = Draft202012Validator(schemas[1], registry=registry)

    def payloads(self):
        context = InstrumentCalibrationContext('tinysa_ultra', 'low', 'sha256:' + '3' * 64, 300000, 12)
        instrument = replace(scoped.profile(None), signature=CalibrationSignature(
            instrument_context=context, frontend_chain='coax-A', backend='instrument',
            device_family='tinysa', rf_port_path='low', sample_rate_hz=None,
            analog_bandwidth_hz=None, gain_mode=None, manual_gain_db=None,
            window_normalization_version=None, fft_unit_convention=None))
        return [json.loads(json.dumps(p.to_dict(), allow_nan=False)) for p in (
            legacy.profile(), scoped.profile(None), instrument,
            scoped.profile(ReceiverChain.RX1), scoped.profile(ReceiverChain.RX2))]

    def test_real_serializers_match_offline_schemas(self):
        for index, payload in enumerate(self.payloads()):
            with self.subTest(version=payload['schema_version']):
                if index == 0:
                    self.legacy.validate(payload)
                    self.assertFalse(self.v2.is_valid(payload))  # Legacy nullable range, not canonical V2.
                else:
                    self.v2.validate(payload)
                self.assertEqual(self.legacy.is_valid(payload), payload['schema_version'] == 1)

    def test_versions_and_receiver_presence_are_strict(self):
        for version in (True, False, 0, 4, '3'):
            payload = self.payloads()[3]
            payload['schema_version'] = version
            self.assertFalse(self.v2.is_valid(payload))
        for chain in (None, 'both', 'RX1', False):
            payload = self.payloads()[3]
            payload['signature']['receiver_chain'] = chain
            self.assertFalse(self.v2.is_valid(payload))
        payload = self.payloads()[3]
        del payload['signature']['receiver_chain']
        self.assertFalse(self.v2.is_valid(payload))
        for index in (0, 1, 2):
            payload = self.payloads()[index]
            payload['signature']['receiver_chain'] = None
            self.assertFalse(self.v2.is_valid(payload))

    def test_instrument_does_not_fabricate_dsp_or_rx(self):
        original = self.payloads()[2]
        for field, value in (('sample_rate_hz', 61440000), ('backend', 'cpu'), ('receiver_chain', 'rx1')):
            payload = deepcopy(original)
            payload['signature'][field] = value
            self.assertFalse(self.v2.is_valid(payload))
        payload = deepcopy(original)
        del payload['signature']['instrument_context']
        self.assertFalse(self.v2.is_valid(payload))
        payload = deepcopy(original)
        payload['signature']['instrument_context']['input_mode'] = 'high'
        self.assertFalse(self.v2.is_valid(payload))

    def test_point_shape_and_unknown_fields_refuse(self):
        for index in range(5):
            for scope in ('root', 'signature', 'point'):
                payload = self.payloads()[index]
                target = payload if scope == 'root' else (
                    payload['signature'] if scope == 'signature' else payload['points'][0])
                target['unexpected'] = 1
                self.assertFalse(self.v2.is_valid(payload))
        payload = self.payloads()[3]
        payload['points'][0]['uncertainty_db'] = -1
        self.assertFalse(self.v2.is_valid(payload))
        del payload['points'][0]['reference_dbm']
        self.assertFalse(self.v2.is_valid(payload))
        payload = self.payloads()[2]
        payload['points'][0]['measured_dbfs'] = 0
        self.assertFalse(self.v2.is_valid(payload))

    def test_instrument_point_limit(self):
        payload = self.payloads()[2]
        payload['points'] = [payload['points'][0]] * 10002
        self.assertTrue(any(error.validator == 'maxItems' for error in self.v2.iter_errors(payload)))

    def test_domain_still_enforces_exact_python_integer(self):
        payload = self.payloads()[3]
        payload['schema_version'] = 3.0
        # JSON Schema treats integral floats as integers; the domain deliberately does not.
        self.v2.validate(payload)
        with self.assertRaises(CalibrationProfileError):
            CalibrationProfile.from_dict(payload)

    def test_domain_still_enforces_ordered_frequency_grid(self):
        payload = self.payloads()[3]
        payload['points'].reverse()
        self.v2.validate(payload)
        with self.assertRaises(CalibrationProfileError):
            CalibrationProfile.from_dict(payload)


if __name__ == '__main__':
    unittest.main()
