"""Observer-only hash substitution must preserve Visual pixels and history."""

from dataclasses import replace
import hashlib
import importlib.util
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication

from scripts.probe_app05_witness_hash import witness_hash_context
from sdr_monitor.ui.v2.spectrum import persistence_projection as worker
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
from sdr_monitor.ui.v2.spectrum.persistence_projection import (
    PersistenceImagePolicy,
    PersistenceImageRequest,
    persistence_input_witness,
    prepare_persistence_image,
    same_persistence_input,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_app05_persistence_worker import view


class WitnessHashProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sha256_context_leaves_product_factory_unchanged(self):
        context, package_version = witness_hash_context("sha256", worker)
        self.assertIsNone(package_version)
        with context:
            self.assertIs(worker.hashlib, hashlib)
        self.assertIs(worker.hashlib, hashlib)
        with self.assertRaises(ValueError):
            witness_hash_context("not-a-hash", worker)

    @unittest.skipUnless(importlib.util.find_spec("blake3"), "isolated blake3 probe venv required")
    def test_blake3_changes_only_digest_not_visual_pixels_or_history(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        prior_view = view(np.full((4, 16), .2, np.float32))
        values = np.full((4, 16), .8, np.float32)
        values[:, 0] = .05
        current_view = view(values)
        product_digest = hashlib.sha256(b"process-global").digest()

        def observe(algorithm):
            context, package_version = witness_hash_context(algorithm, worker)
            with context:
                self.assertEqual(hashlib.sha256(b"process-global").digest(), product_digest)
                prior = prepare_persistence_image(PersistenceImageRequest(prior_view, policy))
                request = PersistenceImageRequest(current_view, policy, prior.as_history(1))
                current = prepare_persistence_image(request)
                history = current.as_history(2)
                restore = prepare_persistence_image(replace(
                    request, history=history, rematerialize=True))
                self.assertIs(restore.image, current.image)
                self.assertTrue(same_persistence_input(
                    history.input_witness, persistence_input_witness(current_view)))
                self.assertEqual(package_version is not None, algorithm == "blake3")
                return (prior.image.copy(), current.image.copy(), restore.image.copy(),
                        history.input_witness.digest)

        sha = observe("sha256")
        candidate = observe("blake3")
        for before, after in zip(sha[:3], candidate[:3]):
            np.testing.assert_array_equal(before.view(np.uint32), after.view(np.uint32))
        self.assertNotEqual(sha[3], candidate[3])
        self.assertIs(worker.hashlib, hashlib)

    @unittest.skipUnless(importlib.util.find_spec("blake3"), "isolated blake3 probe venv required")
    def test_blake3_same_publication_mutation_rejects_rematerialization(self):
        policy = PersistenceImagePolicy(1, PersistenceRenderMode.VISUAL, False)
        publication = view(np.full((4, 16), .5, np.float32))
        context, _ = witness_hash_context("blake3", worker)
        with context:
            first = prepare_persistence_image(PersistenceImageRequest(publication, policy))
            history = first.as_history(1)
            publication.density.setflags(write=True)
            publication.density[0, 0] = .8
            publication.density.setflags(write=False)
            restore = prepare_persistence_image(PersistenceImageRequest(
                publication, policy, history, rematerialize=True))
            fresh = prepare_persistence_image(PersistenceImageRequest(publication, policy, history))
            self.assertIsNot(restore.image, history.image)
            np.testing.assert_array_equal(restore.image, fresh.image)
        self.assertIs(worker.hashlib, hashlib)

    @unittest.skipUnless(importlib.util.find_spec("blake3"), "isolated blake3 probe venv required")
    def test_blake3_gui_fallback_preserves_exact_pixels_and_restore(self):
        prior_view = view(np.full((4, 16), .2, np.float32))
        current_view = view(np.full((4, 16), .8, np.float32))

        def observe(algorithm):
            scene = SpectrumScene()
            try:
                overlay = scene._persistence
                overlay.set_render_mode(PersistenceRenderMode.VISUAL)
                overlay.set_logarithmic(False)
                context, _ = witness_hash_context(algorithm, worker)
                with context:
                    prior = overlay._render_image(prior_view).copy()
                    current = overlay._render_image(current_view).copy()
                    restored = overlay._render_image(current_view, rematerialize=True).copy()
                    digest = overlay._visual_witness.digest
                return prior, current, restored, digest
            finally:
                scene.close()
                scene.deleteLater()
                self.app.processEvents()

        sha = observe("sha256")
        candidate = observe("blake3")
        for before, after in zip(sha[:3], candidate[:3]):
            np.testing.assert_array_equal(before.view(np.uint32), after.view(np.uint32))
        self.assertNotEqual(sha[3], candidate[3])
        np.testing.assert_array_equal(candidate[1], candidate[2])
        self.assertIs(worker.hashlib, hashlib)


if __name__ == "__main__":
    unittest.main()
