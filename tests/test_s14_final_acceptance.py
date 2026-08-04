"""S14/P17 final acceptance contract tests."""

from __future__ import annotations

import unittest

from scripts.s14_final_acceptance import build_matrix


class S14FinalAcceptanceTests(unittest.TestCase):
    def test_final_matrix_is_standalone_and_classifies_missing_toolchain(self) -> None:
        matrix = build_matrix()
        self.assertEqual(matrix["package"], "S14/P17")
        self.assertEqual(matrix["verdict"], "ACCEPT WITH GAPS")
        self.assertEqual(matrix["boundary"]["status"], "PASS")
        self.assertEqual(matrix["release_contract"]["status"], "PASS")
        self.assertIn(matrix["toolchain_probe"]["status"], {"PASS", "NOT_VERIFIED"})
        self.assertTrue(matrix["known_gaps"])

    def test_final_matrix_does_not_scan_legacy_product_sources(self) -> None:
        matrix = build_matrix()
        self.assertEqual(matrix["boundary"]["violations"], [])


if __name__ == "__main__":
    unittest.main()
