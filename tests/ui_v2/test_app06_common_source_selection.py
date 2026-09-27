"""Common Analyzer selection through real application/presenter/Qt seams; no RF."""

from __future__ import annotations

import os
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerPhase, AnalyzerSessionApplicationService
from sdr_monitor.application.analyzer_sources import AnalyzerSourceSelectionApplicationService
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveSessionState
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.device_capabilities import (
    DeviceCapabilityBinding,
    DeviceFamily,
    build_device_capability_inventory,
)
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode as PresentationMode
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerViewModel
from sdr_monitor.ui.v2.view_models.live_view_model import LiveViewModel
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.test_app01_analyzer_session import Live, Sweep
from tests.test_app06_pluto_observation_catalog import _Native
from tests.test_app06_retained_capability_catalog import _Provider
from tests.ui_v2.test_app02_analyzer_view_model import Signal
from tests.ui_v2.test_app02_analyzer_view_model import Sweep as PresentationSweep
from tests.ui_v2.test_live_view_model import FakePresenter, FakeSnapshot


def _foreign_provider(family):
    provider = _Provider(f"fixture.{family.value}", family)
    provider.value = build_device_capability_inventory((), runtimes=(provider.runtime,), bindings=(
        DeviceCapabilityBinding(f"source-{family.value}", family, provider.adapter_id),
    ))
    return provider


def _graph():
    native = _Native()
    native.routes = ("usb:fixture",)
    live = NativeLiveSessionService(native)
    hackrf, tinysa = _foreign_provider(DeviceFamily.HACKRF), _foreign_provider(DeviceFamily.TINYSA)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live), hackrf, tinysa),
                                     control_transaction=live.capability_control_transaction)
    analyzer = AnalyzerSessionApplicationService(live, Sweep([]), start_live=live.start_admitted)
    sources = AnalyzerSourceSelectionApplicationService(catalog, live,
                                                       control_transaction=analyzer.idle_control_operation)
    application = LiveSessionApplicationService(live, analyzer=analyzer, sources=sources,
                                               catalog_close=catalog.close)
    return SimpleNamespace(native=native, live=live, hackrf=hackrf, tinysa=tinysa,
                           catalog=catalog, analyzer=analyzer, sources=sources, application=application)


class CommonSourceApplicationTests(unittest.TestCase):
    def setUp(self):
        self.graph = _graph()
        self.addCleanup(self.graph.application.shutdown)

    def test_discovery_keeps_exact_bindings_and_no_implicit_selection(self):
        graph = self.graph
        choices = graph.application.discover(startup=True)
        self.assertEqual(tuple(choice.family for choice in choices),
                         (DeviceFamily.AD936X, DeviceFamily.HACKRF, DeviceFamily.TINYSA))
        self.assertIsNone(graph.sources.current().selected)
        self.assertEqual(graph.native.scans, ["usb"])
        self.assertEqual(len(graph.native.created), 1)
        for choice in choices:
            self.assertIs(choice.binding, graph.catalog.snapshot().binding_for_source(choice.device_id))
            self.assertIs(choice.runtime, graph.catalog.snapshot().runtime_for_adapter(choice.binding.adapter_id))
            self.assertNotIn("fixture-a", choice.label)
            self.assertNotIn("usb:", choice.label)

    def test_native_selection_observes_once_and_high_rate_profile_remains_real(self):
        graph = self.graph
        choice = graph.application.discover()[0]
        before = len(graph.native.created)
        selected = graph.application.select_device(choice.device_id)
        self.assertEqual(len(graph.native.created), before + 1)
        self.assertEqual(selected.device.device_id, choice.device_id)
        self.assertTrue(graph.sources.current().ad936x_controls_available)
        requested = LiveConfiguration(sample_rate_hz=61.44e6, analog_bandwidth_hz=40e6,
                                      fft_size=4096, gain_db=30, backend=BackendKind.CPU)
        applied = graph.application.apply_configuration(requested)
        self.assertIsNone(applied.error)
        self.assertEqual(applied.applied.applied.sample_rate_hz, 61.44e6)
        self.assertEqual(applied.applied.applied.analog_bandwidth_hz, 40e6)
        self.assertEqual(graph.native.engines, [])  # Apply is staged, not an implicit RX Start.

    def test_foreign_selection_clears_native_publication_and_refuses_pluto_commands(self):
        graph = self.graph
        graph.application.select_device(graph.application.discover()[0].device_id)
        graph.application.apply_configuration(LiveConfiguration())
        before = graph.live.latest_snapshot()
        for family in (DeviceFamily.HACKRF, DeviceFamily.TINYSA):
            with self.subTest(family=family):
                empty = graph.application.select_device(f"source-{family.value}")
                self.assertIs(empty, graph.application.current_snapshot())
                self.assertIsNone(empty.device)
                self.assertIsNone(empty.applied)
                self.assertIsNone(empty.spectrum)
                self.assertIsNone(empty.persistence)
                self.assertEqual(empty.unit, "unavailable")
                self.assertEqual(graph.application.poll_published_snapshots(), [])
                self.assertFalse(graph.application.is_running())
                with patch.object(graph.live, "apply_configuration") as apply, \
                     patch.object(graph.live, "start_admitted") as start:
                    for operation in (
                        graph.application.start,
                        lambda: graph.application.apply_configuration(LiveConfiguration()),
                        lambda: graph.application.preflight_configuration(LiveConfiguration()),
                        lambda: graph.application.start_sweep(ContinuousSweepPlanRequest(100e6, 200e6)),
                    ):
                        with self.assertRaises(LiveAdmissionRejected):
                            operation()
                    apply.assert_not_called()
                    start.assert_not_called()
                self.assertIs(graph.live.latest_snapshot(), before)  # No fake foreign NativeLive state.

    def test_unknown_choice_refuses_before_side_effect_without_erasing_valid_source(self):
        graph = self.graph
        graph.application.select_device(graph.application.discover()[0].device_id)
        current = graph.sources.current()
        count = len(graph.native.created)
        with self.assertRaises(LiveAdmissionRejected):
            graph.application.select_device("missing")
        self.assertIs(graph.sources.current(), current)
        self.assertEqual(len(graph.native.created), count)

    def test_selected_observation_failure_clears_choices_and_redacts_sdk_text(self):
        graph = self.graph
        graph.application.discover()
        graph.application.select_device("source-hackrf")
        revision = graph.sources.current().revision
        graph.hackrf.fail = True
        with self.assertRaises(RuntimeError) as failed:
            graph.application.select_device("source-hackrf")
        self.assertNotIn("PRIVATE", str(failed.exception))
        state = graph.sources.current()
        self.assertEqual(state.choices, ())
        self.assertIsNone(state.selected)
        self.assertEqual(state.revision, revision + 1)
        self.assertEqual(state.refusal, "observation_failed")
        self.assertIs(graph.application.current_snapshot().state, LiveSessionState.ERROR)

    def test_failed_release_is_visible_and_only_explicit_stop_retries_same_provider(self):
        graph = self.graph
        graph.application.discover()
        graph.hackrf.fail = True
        graph.hackrf.cleanup_pending = True
        graph.hackrf.close_fail = True
        with self.assertRaises(RuntimeError):
            graph.application.select_device("source-hackrf")
        calls = list(graph.hackrf.calls)
        self.assertTrue(graph.application.current_snapshot().stop_required)
        for operation in (graph.application.discover, lambda: graph.application.select_device("source-tinysa")):
            with self.assertRaises((RuntimeError, LiveAdmissionRejected)):
                operation()
        self.assertEqual(graph.hackrf.calls, calls)  # No implicit SDK close retry.
        with self.assertRaises(RuntimeError):
            graph.application.stop()
        self.assertTrue(graph.sources.current().release_pending)
        graph.hackrf.close_fail = False
        graph.application.stop()
        self.assertFalse(graph.catalog.cleanup_pending)
        self.assertFalse(graph.sources.current().release_pending)
        self.assertEqual(graph.sources.current().choices, ())
        self.assertEqual(graph.hackrf.calls.count("close"), 2)

    def test_manual_uri_is_explicit_native_selection_not_catalog_alias(self):
        graph = self.graph
        graph.application.discover()
        original = graph.catalog.snapshot()
        graph.native.serial = graph.native.topology_serial = "manual-fixture-b"
        selected = graph.application.select_manual_uri("ip:manual-fixture")
        self.assertIsNone(selected.error)
        self.assertEqual(graph.sources.current().selected.device_id, selected.device.device_id)
        self.assertTrue(graph.sources.current().ad936x_controls_available)
        self.assertIs(graph.catalog.snapshot(), original)
        self.assertIsNone(original.binding_for_source(selected.device.device_id))

    def test_idle_control_reservation_blocks_all_owner_mutations_without_blocking_reads(self):
        events = []
        owner = AnalyzerSessionApplicationService(Live(events), Sweep(events))
        entered, release = threading.Event(), threading.Event()
        failures = []

        def worker():
            try:
                with owner.idle_control_operation():
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("test barrier")
                    raise ValueError("fixture failure")
            except ValueError:
                pass
            except Exception as error:  # noqa: BLE001 - capture background exceptions as explicit test failures.
                failures.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertIs(owner.state.phase, AnalyzerPhase.IDLE)
            self.assertEqual(owner.state.operation_id, 0)
            self.assertTrue(owner.control_busy)
            for operation in (owner.start, owner.stop, lambda: owner.select_mode(AnalyzerMode.SWEEP),
                              lambda: owner.bounded_sweep_operation().__enter__(),
                              lambda: owner.idle_control_operation().__enter__()):
                with self.assertRaises(RuntimeError):
                    operation()
            self.assertEqual(events, [])
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertFalse(owner.control_busy)
        owner.start()
        owner.stop()
        self.assertEqual(events, ["rtbw-start", "rtbw-stop"])

    def test_selection_control_reads_during_probe_have_no_sdk_or_lock_wait(self):
        graph = self.graph
        graph.application.discover()
        entered, release = threading.Event(), threading.Event()
        original = graph.hackrf.observe_source

        def delayed(source_id):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test barrier")
            return original(source_id)

        graph.hackrf.observe_source = delayed
        failures = []

        def select():
            try:
                graph.application.select_device("source-hackrf")
            except Exception as error:  # noqa: BLE001 - capture background exceptions as explicit test failures.
                failures.append(error)

        thread = threading.Thread(target=select)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertIsNone(graph.sources.current().selected)
            self.assertIsNone(graph.application.current_snapshot().device)
            self.assertTrue(graph.analyzer.control_busy)
            with self.assertRaises(RuntimeError):
                graph.application.start()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertIs(graph.sources.current().selected.family, DeviceFamily.HACKRF)


class CommonSourceModelTests(unittest.TestCase):
    def setUp(self):
        self.presenter = FakePresenter()
        self.presenter.source_selection_changed = Signal()
        self.live = LiveViewModel(self.presenter, now_ns=lambda: 1)
        self.sweep = PresentationSweep()
        self.model = AnalyzerViewModel(self.live, self.sweep)
        self.addCleanup(self.model.dispose)
        self.addCleanup(self.live.dispose)
        graph = _graph()
        self.addCleanup(graph.application.shutdown)
        self.choices = graph.application.discover()

    def select(self, index, revision=1):
        choice = self.choices[index]
        selection = AnalyzerSourceSelection(revision, self.choices, choice.device_id)
        self.presenter.source_selection_changed.emit(selection)
        return selection

    def test_foreign_selection_and_late_snapshot_cannot_resurrect_old_frame(self):
        self.select(0)
        old = FakeSnapshot(LiveSessionState.CONNECTED, device=SimpleNamespace(device_id=self.choices[0].device_id))
        self.presenter.snapshot_changed.emit(old)
        self.assertIs(self.live.state.snapshot, old)
        selected = self.select(1, 2)
        self.assertIsNone(self.live.state.snapshot)
        self.assertIsNone(self.model.state.bundle)
        self.presenter.snapshot_changed.emit(old)
        self.assertIsNone(self.live.state.snapshot)
        self.assertIs(self.live.source_selection, selected)
        self.assertFalse(self.model.start())
        self.assertFalse(self.model.apply_configuration(LiveConfiguration()))
        self.assertEqual(self.presenter.start_calls, 0)

    def test_tinysa_is_sweep_only_and_old_native_prepared_state_is_refused(self):
        self.select(0)
        old = FakeSnapshot(LiveSessionState.CONNECTED, device=SimpleNamespace(device_id=self.choices[0].device_id))
        prepared = build_live_view_state(old)
        self.select(2, 2)
        self.assertIs(self.model.state.mode, PresentationMode.SWEEP)
        self.assertFalse(self.model.select_mode(PresentationMode.RTBW))
        self.live._on_prepared_snapshot(prepared)
        self.assertIsNone(self.live.state.snapshot)
        self.assertIsNone(self.model.state.bundle)

    def test_old_selection_ack_is_ignored_and_terminal_release_drops_references(self):
        self.select(0, 1)
        latest = self.select(1, 2)
        self.select(0, 1)
        self.assertIs(self.live.source_selection, latest)
        self.live.dispose()
        self.assertEqual(self.presenter.source_selection_changed.callbacks, [])
        self.live.release_presentation_after_shutdown()
        self.assertIsNone(self.live.source_selection)
        self.assertEqual(self.live.devices, ())

    def test_retained_release_routes_to_catalog_stop_not_sweep_even_without_native_snapshot(self):
        self.select(2)
        pending = AnalyzerSourceSelection(2, release_pending=True, refusal="release_pending")
        self.presenter.source_selection_changed.emit(pending)
        self.assertTrue(self.model.state.stop_required)
        self.assertTrue(self.model.stop())
        self.assertEqual(self.presenter.stop_calls, 1)
        self.assertEqual(self.sweep.calls, [])

    def test_source_choice_validation_is_bounded_and_keeps_existing_references(self):
        choice = self.choices[0]
        self.assertIs(choice.binding, replace(choice).binding)
        consumed = []

        def excessive():
            for index in range(1000):
                consumed.append(index)
                yield replace(choice, binding=replace(choice.binding, source_id=f"source-{index}"))

        with self.assertRaises(ValueError):
            AnalyzerSourceSelection(choices=excessive())
        self.assertEqual(len(consumed), 33)
        for operation in (
            lambda: AnalyzerSourceSelection(True),
            lambda: AnalyzerSourceSelection(choices=(choice, choice)),
            lambda: AnalyzerSourceSelection(choices=(choice,), selected_id="missing"),
            lambda: AnalyzerSourceSelection(release_pending=1),
            lambda: AnalyzerSourceSelection(refusal="PRIVATE SDK details"),
            lambda: replace(choice, label="private\nmultiline"),
            lambda: replace(choice, runtime=self.choices[1].runtime),
        ):
            with self.assertRaises((TypeError, ValueError)):
                operation()


class CommonSourceV2CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("V2 common source completion timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_actual_root_picker_discover_select_apply_and_family_switch(self):
        graph = _graph()
        captures = []

        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            captures.append((composition, args[0]))
            return composition

        services = SimpleNamespace(live_sdr=graph.live, device_catalog=graph.catalog,
                                   sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        composition, presenter = captures[0]
        page = shell._workspace_pages["analyzer"]
        errors = []
        try:
            with patch("sys.excepthook", side_effect=lambda *args: errors.append(args)):
                self.assertIsNotNone(presenter._use_cases._sources)
                self.assertIs(presenter._use_cases._sources._catalog, graph.catalog)
                self.assertIs(composition.view_model.source_selection, presenter.source_selection)
                self.assertEqual(composition.view_model.source_selection.revision, 0)
                self.assertFalse(composition.analyzer_view_model.state.ad936x_controls_available)
                for widget in (page.frequency_bar.fft, page.frequency_bar.gain,
                               page.frequency_bar.apply, page.frequency_bar.cancel):
                    self.assertTrue(widget.isHidden())
                self.assertEqual(graph.native.created, [])  # Opening/navigation are inert.
                page.discover.click()
                self.wait(lambda: page.source.count() == 4 and not composition.view_model.state.busy)
                self.assertEqual(len(graph.native.created), 1)
                native_id = composition.view_model.devices[0].device_id
                page.source.setCurrentIndex(page.source.findData(native_id))
                self.wait(lambda: composition.view_model.source_selection.selected_id == native_id
                          and not composition.view_model.state.busy)
                self.assertEqual(len(graph.native.created), 2)
                for widget in (page.frequency_bar.fft, page.frequency_bar.gain,
                               page.frequency_bar.apply, page.frequency_bar.cancel):
                    self.assertFalse(widget.isHidden())
                page.drawer._sample_rate.setValue(61.44)
                page.drawer._fft.setValue(4096)
                page.drawer._backend.setCurrentIndex(page.drawer._backend.findData(BackendKind.CPU.value))
                page.drawer.apply_draft()
                self.wait(lambda: composition.view_model.state.has_applied_configuration
                          and not composition.view_model.state.busy and not page.drawer.pending)
                self.assertEqual(graph.live.latest_snapshot().applied.applied.sample_rate_hz, 61.44e6)
                page.drawer._gain.setValue(45)
                self.assertTrue(page.drawer.dirty)
                old_snapshot = graph.live.latest_snapshot()
                for family in (DeviceFamily.HACKRF, DeviceFamily.TINYSA):
                    page.source.setCurrentIndex(page.source.findData(f"source-{family.value}"))
                    self.wait(lambda family=family: composition.view_model.source_selection.selected.family is family
                              and not composition.view_model.state.busy)
                    self.assertFalse(page.primary.isEnabled())
                    self.assertFalse(page.drawer.can_apply)
                    self.assertFalse(page.drawer.dirty)
                    self.assertIsNone(page.drawer._base_identity)
                    self.assertTrue(page.drawer._sample_rate.isHidden())
                    self.assertTrue(page.drawer._rf_bandwidth.isHidden())
                    self.assertTrue(page.drawer._backend.isHidden())
                    for widget in (page.frequency_bar.fft, page.frequency_bar.gain,
                                   page.frequency_bar.apply, page.frequency_bar.cancel):
                        self.assertTrue(widget.isHidden())
                    self.assertIn(text("analyzer.source.family_path_pending"), page.source_summary.text())
                    self.assertIsNone(composition.view_model.state.snapshot.device)
                    composition.view_model._on_prepared_snapshot(build_live_view_state(old_snapshot))
                    self.assertIsNone(composition.view_model.state.snapshot.device)
                self.assertIs(composition.analyzer_view_model.state.mode, PresentationMode.SWEEP)
                previous_locale = current_locale()
                try:
                    set_active_locale(UiLocale.EN)
                    page.set_locale()
                    self.assertIn("Source selected", page.source_summary.text())
                    self.assertIn("AD936x", page.drawer._use_uri.text())
                finally:
                    set_active_locale(previous_locale)
                    page.set_locale()
                page.source.setCurrentIndex(page.source.findData(native_id))
                self.wait(lambda: composition.view_model.source_selection.selected_id == native_id
                          and not composition.view_model.state.busy)
                for widget in (page.frequency_bar.fft, page.frequency_bar.gain,
                               page.frequency_bar.apply, page.frequency_bar.cancel):
                    self.assertFalse(widget.isHidden())
                self.assertFalse(page.drawer.dirty)
                self.assertNotEqual(page.frequency_bar.gain.value(), 45)
                # Fail selected read-only cleanup while the local mode is Sweep.
                # The actual Stop button must release the catalog, not dispatch
                # cancellation to a receiver/Sweep it does not own.
                graph.hackrf.fail = True
                graph.hackrf.cleanup_pending = True
                graph.hackrf.close_fail = True
                page.source.setCurrentIndex(page.source.findData("source-hackrf"))
                self.wait(lambda: composition.analyzer_view_model.state.stop_required
                          and not composition.view_model.state.busy)
                self.assertTrue(page.primary.isEnabled())
                page.primary.click()
                self.wait(lambda: graph.hackrf.calls.count("close") == 1
                          and not composition.view_model.state.busy)
                self.assertTrue(composition.analyzer_view_model.state.stop_required)
                graph.hackrf.close_fail = False
                page.primary.click()
                self.wait(lambda: not composition.analyzer_view_model.state.stop_required
                          and not composition.view_model.state.busy)
                self.assertFalse(graph.catalog.cleanup_pending)
                self.assertEqual(graph.hackrf.calls.count("close"), 2)
                self.assertEqual(graph.native.engines, [])
        finally:
            shell.close()
            self.wait(lambda: shell._is_closed)
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()
        self.assertEqual(errors, [], "Qt callback errors are not a passing UI test")


if __name__ == "__main__":
    unittest.main()
