"""Explicit single-RX profile semantics, not hardware calibration proof."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from sdr_monitor.domain.calibration import (
    CalibrationSignature, CalibrationPoint, CalibrationProfile, CalibrationProfileError,
    CalibrationStatus, InstrumentCalibrationContext, apply_calibration, check_applicability,
)
from sdr_monitor.domain.receiver_topology import ReceiverChain, ReceiverChainSelection
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.calibration_service import CalibrationService


def signature(chain=ReceiverChain.RX1):
    return CalibrationSignature(device_serial='serial', device_family='ad936x', adapter_id='pluto',
        device_identity_key='sha256:'+'1'*64,firmware_fingerprint='sha256:'+'2'*64,
        frontend_chain='physical-frontend',receiver_chain=chain)


def profile(chain=ReceiverChain.RX1):
    return CalibrationProfile('rx-calibration',1,signature(chain),
        (CalibrationPoint(100.0,1.0,0.2),CalibrationPoint(200.0,2.0,0.3)))


class ReceiverCalibrationTests(unittest.TestCase):
    def test_same_chain_applies_other_chain_and_unknown_refuse(self):
        item = profile()
        self.assertTrue(check_applicability(item,signature()).applicable)
        for chain in (ReceiverChain.RX2,None):
            result = check_applicability(item,signature(chain))
            self.assertFalse(result.applicable)
            self.assertIn('Receiver chain',result.reason)

    def test_legacy_serialization_unchanged_and_no_auto_rx1(self):
        item = profile(None)
        payload = item.to_dict()
        self.assertEqual(payload['schema_version'],1)
        self.assertNotIn('receiver_chain',payload['signature'])
        loaded = CalibrationProfile.from_dict(json.loads(json.dumps(payload)))
        self.assertIsNone(loaded.signature.receiver_chain)
        self.assertEqual(loaded.fingerprint,item.fingerprint)
        self.assertFalse(check_applicability(loaded,signature()).applicable)
        self.assertTrue(check_applicability(loaded,signature(None)).applicable)

    def test_explicit_rx_schema3_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(Path(directory))
            item = profile(ReceiverChain.RX2)
            self.assertEqual(item.to_dict()['schema_version'],3)
            store.save(item)
            loaded = store.load(item.profile_id,item.profile_version)
            self.assertIs(loaded.signature.receiver_chain,ReceiverChain.RX2)
            self.assertEqual(loaded.fingerprint,item.fingerprint)

    def test_invalid_typed_constructor_cannot_use_both_or_label(self):
        for chain in ('rx1','RX2',ReceiverChainSelection.BOTH,ReceiverChainSelection.RX1,True):
            with self.subTest(chain=chain),self.assertRaises(CalibrationProfileError):
                signature(chain)

    def test_invalid_json_rx_refused(self):
        for chain in ('both','RX1','rx1 ',False,{},1):
            payload = profile().to_dict()
            payload['signature']['receiver_chain'] = chain
            with self.subTest(chain=chain),self.assertRaises(CalibrationProfileError):
                CalibrationProfile.from_dict(payload)

    def test_schema_cannot_hide_or_fabricate_rx(self):
        for version in (1,2,3.0,True,4):
            payload = profile().to_dict()
            payload['schema_version'] = version
            with self.subTest(version=version),self.assertRaises(CalibrationProfileError):
                CalibrationProfile.from_dict(payload)
        for raw in (None,'missing'):
            payload = profile().to_dict()
            if raw is None:
                payload['signature']['receiver_chain'] = None
            else:
                del payload['signature']['receiver_chain']
            with self.assertRaises(CalibrationProfileError):
                CalibrationProfile.from_dict(payload)

    def test_fingerprints_differ_by_rx_only(self):
        item = profile()
        other = replace(item,signature=signature(ReceiverChain.RX2))
        self.assertNotEqual(item.fingerprint,other.fingerprint)

    def test_receiver_change_deactivates_service_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CalibrationService(CalibrationProfileStore(Path(directory)))
            item = service.finalize_profile(profile())
            service.set_current_settings(signature())
            service.select_active_profile(item)
            service.set_current_settings(signature(ReceiverChain.RX2))
            self.assertIsNone(service.active_profile())

    def test_wrong_rx_never_gets_dbm_unit(self):
        matched = apply_calibration([0.0],[100.0],profile(),signature())
        wrong = apply_calibration([0.0],[100.0],profile(),signature(ReceiverChain.RX2))
        self.assertEqual(matched.unit,'dBm/bin')
        self.assertEqual(wrong.unit,'dBFS/bin')
        self.assertIs(wrong.status,CalibrationStatus.INVALID)

    def test_unknown_provenance_not_relaxed_by_known_rx(self):
        self.assertFalse(check_applicability(profile(),replace(signature(),device_identity_key='unknown')).applicable)

    def test_instrument_correction_cannot_claim_digital_rx(self):
        context = InstrumentCalibrationContext('tinysa_ultra','low','sha256:'+'3'*64,300_000,12)
        with self.assertRaisesRegex(CalibrationProfileError,'instrument correction cannot declare'):
            CalibrationSignature(instrument_context=context,receiver_chain=ReceiverChain.RX1)
