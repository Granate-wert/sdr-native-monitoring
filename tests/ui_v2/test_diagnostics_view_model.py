"""UI2-10A contract tests for deferred Diagnostics presenter ownership."""

from __future__ import annotations

import unittest
from pathlib import Path

from sdr_monitor.domain import (
    DiagnosticCard,
    DiagnosticStatus,
    DiagnosticsSnapshot,
    SelfTestResult,
    SupportBundleResult,
)
from sdr_monitor.ui.v2.view_models.diagnostics_view_model import DeferredDiagnosticsViewModel


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: object) -> None:
        self.callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakeDiagnosticsPresenter:
    def __init__(self) -> None:
        self.snapshot_changed = FakeSignal()
        self.self_tests_changed = FakeSignal()
        self.bundle_ready = FakeSignal()
        self.task_failed = FakeSignal()
        self.busy_changed = FakeSignal()
        self.refresh_calls = 0
        self.self_test_calls = 0
        self.cancel_calls = 0
        self.bundle_paths: list[Path] = []
        self.shutdown_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def run_self_tests(self) -> None:
        self.self_test_calls += 1

    def cancel(self) -> None:
        self.cancel_calls += 1

    def export_bundle(self, output_dir: Path) -> None:
        self.bundle_paths.append(output_dir)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _snapshot() -> DiagnosticsSnapshot:
    return DiagnosticsSnapshot(
        platform={"os": "Windows"},
        cards=(
            DiagnosticCard(
                "cpu",
                "CPU backend",
                DiagnosticStatus.PASS,
                "portable",
                "not run",
                "Reference CPU path available",
                "Run self-test",
            ),
        ),
        errors=(),
        metrics={"queued": 0},
    )


class DeferredDiagnosticsViewModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.presenter = FakeDiagnosticsPresenter()
        self.factory_calls = 0

        def factory() -> FakeDiagnosticsPresenter:
            self.factory_calls += 1
            return self.presenter

        self.view_model = DeferredDiagnosticsViewModel(factory)

    def test_factory_is_deferred_until_explicit_load_then_snapshot_is_immutable(self) -> None:
        self.assertEqual(self.factory_calls, 0)
        self.assertFalse(self.view_model.state.loaded)
        self.assertTrue(self.view_model.load())
        self.assertEqual(self.factory_calls, 1)
        self.assertEqual(self.presenter.refresh_calls, 1)
        self.presenter.snapshot_changed.emit(_snapshot())
        self.assertTrue(self.view_model.state.loaded)
        self.assertEqual(self.view_model.state.snapshot, _snapshot())
        self.assertTrue(self.view_model.refresh())
        self.assertEqual(self.factory_calls, 1)
        self.assertEqual(self.presenter.refresh_calls, 2)

    def test_only_admitted_offline_commands_are_forwarded_after_load(self) -> None:
        self.assertFalse(self.view_model.run_self_tests())
        self.assertFalse(self.view_model.export_bundle(""))
        self.assertTrue(self.view_model.load())
        self.assertTrue(self.view_model.run_self_tests())
        self.assertTrue(self.view_model.export_bundle(" C:/support "))
        self.assertEqual(self.presenter.self_test_calls, 1)
        self.assertEqual(self.presenter.bundle_paths, [Path("C:/support")])
        result = SelfTestResult("cpu", DiagnosticStatus.PASS, "ok", 1.5)
        self.presenter.self_tests_changed.emit([result])
        self.presenter.bundle_ready.emit(SupportBundleResult("C:/support/bundle.json", ("platform",), True))
        self.assertEqual(self.view_model.state.self_tests, (result,))
        self.assertEqual(self.view_model.state.bundle, SupportBundleResult("C:/support/bundle.json", ("platform",), True))

    def test_busy_prevents_new_commands_and_close_but_allows_explicit_cancel(self) -> None:
        self.view_model.load()
        self.presenter.busy_changed.emit(True)
        self.assertFalse(self.view_model.state.can_close)
        self.assertFalse(self.view_model.refresh())
        self.assertFalse(self.view_model.run_self_tests())
        self.assertTrue(self.view_model.cancel())
        self.assertEqual(self.presenter.cancel_calls, 1)
        self.presenter.busy_changed.emit(False)
        self.assertTrue(self.view_model.state.can_close)

    def test_dispose_does_not_create_presenter_and_shuts_down_created_presenter_once(self) -> None:
        never_loaded = DeferredDiagnosticsViewModel(lambda: self.presenter)
        never_loaded.dispose()
        self.assertEqual(self.presenter.shutdown_calls, 0)
        self.view_model.load()
        self.view_model.dispose()
        self.view_model.dispose()
        self.assertEqual(self.presenter.shutdown_calls, 1)
        self.assertFalse(self.view_model.refresh())
