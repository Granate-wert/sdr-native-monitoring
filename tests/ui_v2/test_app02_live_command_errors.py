"""APP-02 command failures stay visible without mutating measurement truth."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import cast

from sdr_monitor.ui.v2.view_models.live_view_model import LivePresenterPort, LiveViewModel


class Signal:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> object:
        self.callbacks.append(callback)
        return callback

    def disconnect(self, callback: Callable[..., None]) -> object:
        self.callbacks.remove(callback)
        return callback

    def emit(self, value) -> None:
        for callback in tuple(self.callbacks):
            callback(value)


class Presenter:
    def __init__(self) -> None:
        self.devices_discovered = Signal()
        self.snapshot_changed = Signal()
        self.task_failed = Signal()
        self.busy_changed = Signal()
        self.render_ready = Signal()
        self.calls: list[object] = []

    def discover_devices(self) -> None:
        self.calls.append("discover")

    def select_device(self, identifier: str) -> None:
        self.calls.append(("select", identifier))

    def select_manual_uri(self, uri: str) -> None:
        self.calls.append(("uri", uri))

    def apply_configuration(self, configuration: object) -> None:
        self.calls.append(("apply", configuration))

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")


class LiveCommandErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.presenter = Presenter()
        self.model = LiveViewModel(cast(LivePresenterPort, self.presenter), now_ns=lambda: 10)

    def tearDown(self) -> None:
        self.model.dispose()

    def test_select_failure_is_visible_and_snapshot_updates_do_not_erase_it(self) -> None:
        snapshot = object()
        self.presenter.snapshot_changed.emit(snapshot)
        original_loss = self.model.state.loss
        self.assertTrue(self.model.select_device("source-b"))
        self.presenter.task_failed.emit("select failed")

        self.assertIs(self.model.state.snapshot, snapshot)
        self.assertEqual(self.model.state.error_label, "select failed")
        self.assertEqual(self.model.state.error_kind, "command-not-measurement")
        self.assertEqual(self.model.state.loss, original_loss)
        self.presenter.render_ready.emit(snapshot)
        self.model.refresh_presentation()
        self.assertEqual(self.model.state.error_label, "select failed")

    def test_next_explicit_command_clears_error_without_retry_invention(self) -> None:
        self.presenter.task_failed.emit("apply failed")
        self.assertEqual(self.model.state.error_label, "apply failed")

        payload = object()
        self.assertTrue(self.model.apply_configuration(payload))
        self.assertIsNone(self.model.state.error_label)
        self.assertIsNone(self.model.state.error_kind)
        self.assertEqual(self.presenter.calls, [("apply", payload)])

    def test_locale_refresh_preserves_exact_command_error(self) -> None:
        self.presenter.task_failed.emit("USB route unavailable")
        before = self.model.state
        self.model.refresh_presentation()
        after = self.model.state

        self.assertEqual(after.error_label, "USB route unavailable")
        self.assertEqual(after.error_kind, "command-not-measurement")
        self.assertIs(after.snapshot, before.snapshot)
        self.assertEqual(after.primary_action, before.primary_action)


if __name__ == "__main__":
    unittest.main()
