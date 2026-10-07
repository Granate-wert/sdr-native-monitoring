"""Standalone V2 backend storage race; no legacy/UI/RF admission claim."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier
import tempfile
import unittest
from unittest.mock import patch

from sdr_monitor.domain.calibration import CalibrationPoint, CalibrationProfile, CalibrationProfileError, CalibrationSignature
from sdr_monitor.services.calibration_store import CalibrationProfileStore


def profile(correction=1.0):
    return CalibrationProfile('immutable-race',1,CalibrationSignature(),
        (CalibrationPoint(100.0,correction,0.2),CalibrationPoint(200.0,correction,0.3)))


class ImmutablePublicationTests(unittest.TestCase):
    def test_winner_published_after_absence_check_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(Path(directory))
            winner = store.save(profile(2.0))
            target = store._path(winner.profile_id,winner.profile_version)
            original_exists = Path.exists
            def stale_absence(path):
                return False if path == target else original_exists(path)
            # Exact publication race state: writer observed absence, competitor
            # finalized this version before publication. Do not trust exists().
            with patch.object(Path,'exists',stale_absence):
                with self.assertRaises(CalibrationProfileError):
                    store.save(profile(3.0))
            self.assertEqual(store.load(winner.profile_id,winner.profile_version).fingerprint,winner.fingerprint)
            self.assertEqual(list(target.parent.glob('*.part')),[])

    def test_identical_winner_is_idempotent_after_stale_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(Path(directory))
            original = profile()
            store.save(original)
            target = store._path(original.profile_id,original.profile_version)
            exists = Path.exists
            with patch.object(Path,'exists',lambda path: False if path == target else exists(path)):
                self.assertEqual(store.save(original).fingerprint,original.fingerprint)
            self.assertEqual(list(target.parent.glob('*.part')),[])

    def test_existing_different_version_refuses_normally(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(Path(directory))
            store.save(profile())
            with self.assertRaises(CalibrationProfileError):
                store.save(profile(2.0))

    def test_new_version_is_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(Path(directory))
            first = store.save(profile())
            second = store.save(replace(profile(2.0),profile_version=2))
            self.assertNotEqual(first.fingerprint,second.fingerprint)
            self.assertEqual(len(store.list_profiles()),2)

    def test_two_real_writers_publish_only_one_complete_version(self):
        with tempfile.TemporaryDirectory() as directory:
            stores = [CalibrationProfileStore(Path(directory)) for _ in range(2)]
            barrier = Barrier(2)
            real_link = os.link
            def competing_link(source,target):
                barrier.wait(timeout=5)
                return real_link(source,target)
            def save(index):
                try:
                    return stores[index].save(profile(float(index+1))).fingerprint
                except CalibrationProfileError:
                    return None
            with patch('sdr_monitor.services.calibration_store.os.link',competing_link), ThreadPoolExecutor(2) as executor:
                results = list(executor.map(save,range(2)))
            winners = [result for result in results if result is not None]
            self.assertEqual(len(winners),1)
            self.assertEqual(stores[0].load('immutable-race',1).fingerprint,winners[0])
            self.assertEqual(list(Path(directory).rglob('*.part')),[])

    def test_unsupported_publication_fails_closed_and_cleans_only_owned_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(Path(directory))
            with patch('sdr_monitor.services.calibration_store.os.link',side_effect=OSError('unsupported')):
                with self.assertRaisesRegex(CalibrationProfileError,'publication failed'):
                    store.save(profile())
            self.assertEqual(list(Path(directory).rglob('*.json')),[])
            self.assertEqual(list(Path(directory).rglob('*.part')),[])
