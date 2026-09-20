"""Observer identities are weak, bounded and never borrowed from another source."""
import unittest
import weakref

import numpy as np

from scripts.profile_app05_persistence import DensityRecords


class DensityRecordsTests(unittest.TestCase):
    def test_exact_source_pair_and_repeated_upload_keep_original_conversion(self):
        records = DensityRecords()
        density = np.zeros((2, 4))
        records.converted(density, (1, 2, 3, 4), 1, 2, False)
        records.uploaded(density, 3, 4, 250, True)
        records.uploaded(density, 5, 6, 200, True)
        first, second = records.uploads
        self.assertEqual(first["identity"], (1, 2, 3, 4))
        self.assertTrue(first["first"])
        self.assertFalse(second["first"])
        self.assertEqual(first["other_upload_ms"], 750)
        self.assertEqual(second["conversion_to_upload_ms"], 3000)
        self.assertEqual(first["conversion_ms"], 1000)
        reference = weakref.ref(density)
        del density
        self.assertIsNone(reference())

    def test_missing_or_reused_object_id_cannot_borrow_conversion(self):
        records = DensityRecords()
        first, other = np.zeros((2, 4)), np.ones((2, 4))
        records.converted(first, (1, 2), 1, 2, False)
        records.uploaded(other, 3, 4, 250, True)
        # Simulate ID-slot reuse while the old weak witness is stale/different.
        records.sources[id(other)] = records.sources[id(first)]
        records.uploaded(other, 3, 4, 250, True)
        self.assertEqual(records.missing, 2)
        self.assertEqual(len(records.uploads), 0)

    def test_registry_and_samples_stay_bounded(self):
        records = DensityRecords(2)
        sources = [np.full((2, 4), i) for i in range(5)]
        for i, density in enumerate(sources):
            records.converted(density, (i,), i, i + .1, False)
            records.uploaded(density, i + .2, i + .3, 50, True)
        self.assertEqual(records.serial, 5)
        self.assertEqual(records.evictions, 3)
        self.assertEqual(len(records.sources), 2)
        self.assertEqual(len(records.conversions), 2)
        self.assertEqual(len(records.uploads), 2)
        records.uploaded(sources[0], 7, 8, 50, True)
        self.assertEqual(records.missing, 1)


if __name__ == "__main__":
    unittest.main()
