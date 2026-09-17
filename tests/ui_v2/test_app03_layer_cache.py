"""Presentation reuse is bounded, identity-only and downstream of coherence."""

from dataclasses import replace
from unittest.mock import patch
import unittest

from sdr_monitor.ui.v2.state.analyzer_layer_cache import AnalyzerLayerCache
from sdr_monitor.ui.v2.state.analyzer_layers import persistence_density_from_native
from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
from sdr_monitor.ui.v2.view_models.live_view_model import LiveViewModel
import tests.test_app03_persistence_cadence as fixture
from tests.ui_v2.test_live_view_model import FakePresenter


class LayerCacheTests(unittest.TestCase):
    def test_identical_layers_convert_once_across_metadata_and_locale_refresh(self):
        snapshot = fixture.PersistenceCadenceTests().snapshot()
        presenter = FakePresenter()
        model = LiveViewModel(presenter, now_ns=lambda: 100)
        self.addCleanup(model.dispose)
        target = "sdr_monitor.ui.v2.state.analyzer_layer_cache.persistence_density_from_native"
        with patch(target, wraps=persistence_density_from_native) as convert:
            presenter.render_ready.emit(snapshot)
            first = model.state
            for index in range(100):
                presenter.snapshot_changed.emit(replace(snapshot, sequence=index + 2))
                model.refresh_presentation()
                self.assertIs(model.state.persistence_frame, first.persistence_frame)
                self.assertIs(model.state.waterfall_line, first.waterfall_line)
            self.assertEqual(convert.call_count, 1)
        self.doCleanups()
        self.assertIsNone(model._layer_cache._density_source)
        self.assertIsNone(model._layer_cache._waterfall_source)

    def test_replacement_and_clear_release_old_pair_without_history(self):
        snapshot = fixture.PersistenceCadenceTests().snapshot()
        cache = AnalyzerLayerCache()
        first = cache.persistence(snapshot.persistence)
        changed = replace(snapshot.persistence, probability_scale=0.2)
        second = cache.persistence(changed)
        self.assertIsNot(first, second)
        self.assertIs(cache._density_source, changed)
        cache.clear()
        self.assertIsNone(cache._density)
        self.assertIsNone(cache._density_source)
        self.assertIsNot(cache.persistence(snapshot.persistence), first)

    def test_cache_never_bypasses_coherence_for_reused_layer(self):
        snapshot = fixture.PersistenceCadenceTests().snapshot()
        cache = AnalyzerLayerCache()
        accepted = build_live_view_state(snapshot, layer_cache=cache)
        self.assertIsNotNone(accepted.persistence_frame)
        wrong = replace(snapshot, spectrum=replace(snapshot.spectrum, acquisition_epoch=4))
        rejected = build_live_view_state(wrong, layer_cache=cache)
        self.assertIsNone(rejected.persistence_frame)
        self.assertEqual(rejected.measurement_unavailable_reason, "persistence_identity_mismatch")
        self.assertIsNone(cache._density_source)
        build_live_view_state(None, layer_cache=cache)
        self.assertIsNone(cache._waterfall_source)
