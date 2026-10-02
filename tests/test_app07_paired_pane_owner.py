"""Paired pane routing through actual native/Live/V2 graph, MOCK IIO only."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.analyzer import PairedCaptureMetadata
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.services.pane_resource_session import PaneCaptureAdmission
from tests.test_app07_pane_resource_session import live_frame, readonly

from tests.test_app07_native_paired_live import MOCK, MODULE, ROOT


class PairedAdmissionTests(unittest.TestCase):
    def admission(self, **changes):
        value = PaneCaptureAdmission("capture", "selected-device", CaptureMeasurementMode.RTBW,
            "dBFS/bin", ("endpoint-a", "endpoint-b"), 1, session_id="session",
            config_generation=1, sample_rate_hz=61_440_000., fft_size=4096, hop_size=2048,
            endpoint_source_ids=(("endpoint-a", "producer-a"), ("endpoint-b", "producer-b")))
        return replace(value, **changes)

    def test_exact_endpoint_producers_are_not_operational_device_aliases(self):
        value = self.admission()
        self.assertEqual(value.producer_source_id("endpoint-a"), "producer-a")
        self.assertEqual(value.producer_source_id("endpoint-b"), "producer-b")
        self.assertEqual(value.source_id, "selected-device")
        with self.assertRaises(ValueError):
            value.producer_source_id("foreign")

    def test_malformed_incomplete_duplicate_and_reordered_binding_refuses(self):
        for bindings in (
                (("endpoint-a", "producer-a"),),
                (("endpoint-b", "producer-b"), ("endpoint-a", "producer-a")),
                (("endpoint-a", "producer-a"), ("endpoint-b", "producer-a")),
                (("endpoint-a", "producer-a"), ("foreign", "producer-b")),
                (("endpoint-a", " producer-a"), ("endpoint-b", "producer-b")),
                (("endpoint-a", "producer-a"), ("endpoint-b",)),
                (("endpoint-a", "producer-a"), ["endpoint-b", "producer-b"]),
        ):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                self.admission(endpoint_source_ids=bindings)

    def test_legacy_single_source_receipts_keep_existing_contract(self):
        value = self.admission(endpoint_source_ids=())
        self.assertEqual(value.producer_source_id("endpoint-a"), value.source_id)

    def test_sync_metadata_never_accepts_unknown_negative_bool_or_fraction(self):
        self.assertEqual(PairedCaptureMetadata(2, 1024, 1).synchronization_epoch, 2)
        for value in (-1, True, None, 1.5):
            for position in range(3):
                counters = [0, 0, 0]
                counters[position] = value
                with self.subTest(position=position, value=value), self.assertRaises(ValueError):
                    PairedCaptureMetadata(*counters)

    def test_native_zero_power_is_not_rejected_or_replaced_by_a_floor_or_gap(self):
        import numpy as np
        frame = live_frame("actual", "session", 1)
        values = np.full(frame.spectrum.fft_size, -100., dtype=np.float32)
        values[0], values[1] = -np.inf, np.nan
        source = replace(frame.spectrum, values=readonly(values, np.float32))
        measured = replace(frame, spectrum=source, identity=None)
        self.assertIs(measured.spectrum, source)
        self.assertTrue(np.isneginf(measured.values[0]))
        self.assertTrue(np.isnan(measured.values[1]))
        values = values.copy()
        values[0] = np.inf
        with self.assertRaisesRegex(ValueError, r"\+infinity"):
            replace(frame, spectrum=replace(source, values=readonly(values, np.float32)), identity=None)


def run_native_case(path: str, case: str) -> None:
    import ctypes

    from sdr_monitor.domain import BackendKind, LiveConfiguration
    from sdr_monitor.domain.pane_scheduler import PaneLayoutSlot, compile_pane_layout
    from sdr_monitor.domain.receiver_topology import AcquisitionGroup, ReceiverChainSelection, ReceiverEndpoint
    from sdr_monitor.services.native_live import NativeLiveSessionService
    from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
    from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
    from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
    from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
    from sdr_monitor.ui.v2_pane_composition import compose_v2_pane_resource_session
    from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
    from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
    from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep
    from tests.test_app07_shared_capture_schedule import pane, profile

    module_path = Path(path).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    assert spec is not None and spec.loader is not None
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    assert Path(native.__file__).resolve() == module_path
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
                                      control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, device_catalog=catalog, analyzer_display=_UnusedSweep()))
    app = graph.live
    session = None
    preparer = None
    board = None
    qt_app = None
    settings_directory = None
    leases = ReceiverLeaseManager()
    try:
        app.discover(startup=True)
        selected = app.select_manual_uri("usb:mock")
        assert selected.error is None, selected.error
        selected = app.apply_configuration(LiveConfiguration(
            center_hz=2_450_000_000., sample_rate_hz=61_440_000.,
            analog_bandwidth_hz=56_000_000., gain_db=20., fft_size=4096, backend=BackendKind.CPU))
        assert selected.error is None and selected.device is not None
        source = selected.device.device_id
        endpoints = (ReceiverEndpoint("caller:rx1", source, "physical", ReceiverChainSelection.RX1),
                     ReceiverEndpoint("caller:rx2", source, "physical", ReceiverChainSelection.RX2))
        groups = (AcquisitionGroup("group", "physical", endpoints),)
        # Use the common scheduler and concrete V2 graph composition, not a
        # private session or a fabricated producer adapter.
        slots = (PaneLayoutSlot(1, pane("left", "caller:rx1", 2_440e6, 2_450e6)),
                 PaneLayoutSlot(2, pane("right", "caller:rx2", 2_450e6, 2_460e6)),
                 PaneLayoutSlot(3), PaneLayoutSlot(4))
        layout = compile_pane_layout(slots, groups,
            {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        writes = hooks.mock_iio_rf_mutation_calls()
        if case in {"empty-serial", "single-layout"}:
            try:
                compose_v2_pane_resource_session(layout, groups, {"physical": graph}, leases)
            except ValueError:
                pass
            else:
                raise AssertionError("unqualified pair must refuse before leasing or RF")
            assert hooks.mock_iio_rf_mutation_calls() == writes
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            assert leases.active_resource_count == 0
            return
        session = compose_v2_pane_resource_session(layout, groups, {"physical": graph}, leases)
        assert session is not None
        # Preserve the first MOCK owner cause before the public fixed refusal
        # redacts it; this is test-only instrumentation, never a product retry.
        owner = session._runtimes["physical"].owner
        original_poll = owner.poll_bundles
        def diagnostic_poll():
            try:
                return original_poll()
            except Exception:
                traceback.print_exc()
                raise
        owner.poll_bundles = diagnostic_poll
        preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget(),
            admitted_producer_source_id=session.admitted_producer_source_id)
        if case == "qt-rearm":
            from PySide6.QtCore import QSettings
            from PySide6.QtWidgets import QApplication
            from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
            qt_app = QApplication.instance() or QApplication([])
            settings_directory = tempfile.TemporaryDirectory(prefix="app07-paired-qt-")
            settings = QSettings(str(Path(settings_directory.name) / "settings.ini"), QSettings.Format.IniFormat)
            board = IndependentPaneBoardV2(preparer, settings=settings)
            board.resize(1600, 920)
            board.show()
            qt_app.processEvents()
        assert session.admitted_producer_source_id("physical", "caller:rx1") is None
        assert hooks.mock_iio_rf_mutation_calls() == writes
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert session.preview()[0].affected_pane_ids == ("left", "right")
        session.apply()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        first = session.start_resource("physical")
        assert session.admitted_producer_source_id("physical", "caller:rx1") == "caller:rx1"
        assert session.admitted_producer_source_id("physical", "caller:rx2") == "caller:rx2"
        assert session.admitted_producer_source_id("physical", "foreign") is None
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1
        assert leases.active_resource_count == 1
        epoch = service.latest_snapshot().acquisition_epoch
        deadline = time.monotonic() + 10
        deliveries = ()
        while not deliveries:
            deliveries = session.poll_resource("physical")
            assert time.monotonic() < deadline, "bounded paired pane delivery deadline"
            time.sleep(.005)
        assert tuple(item.pane_id for item in deliveries) == ("left", "right")
        left, right = (item.bundle for item in deliveries)
        assert left.identity.source_id == "caller:rx1" and right.identity.source_id == "caller:rx2"
        assert left.receiver_id == "RX1" and right.receiver_id == "RX2"
        assert left.acquisition_epoch == right.acquisition_epoch == epoch
        assert left.paired_capture == right.paired_capture and left.paired_capture is not None
        assert not (left.values == right.values).all()
        prepared = tuple(preparer.prepare(item) for item in deliveries)
        assert all(item.binding.source_id == source for item in prepared)
        assert preparer.paired_resource_ids == frozenset({"physical"})
        assert tuple(item.binding.receiver_selection for item in prepared) == (
            ReceiverChainSelection.RX1, ReceiverChainSelection.RX2)
        assert tuple(item.producer_source_id for item in prepared) == ("caller:rx1", "caller:rx2")
        assert all(item.waterfall is not None and item.spectrum.view.source_frame is item.bundle for item in prepared)
        assert prepared[0].bundle.paired_capture == prepared[1].bundle.paired_capture
        # A typed BOTH group cannot downgrade to a single RX packet, nor may
        # a source-correct frame impersonate the other selected chain.
        for malformed in (
                replace(deliveries[0], bundle=replace(left, paired_capture=None)),
                replace(deliveries[0], bundle=replace(left, spectrum=replace(
                    left.spectrum, receiver_id="RX2"), receiver_id="RX2", identity=None,
                    persistence=None, waterfall_line=None))):
            try:
                preparer.prepare(malformed)
            except ValueError:
                pass
            else:
                raise AssertionError("typed pair or receiver mismatch must refuse before preparation")
        if board is not None:
            assert board.apply_prepared(prepared[0])
            assert board.apply_prepared(prepared[1])
            qt_app.processEvents()
            assert board.pane(1).last_bundle is left
            assert board.pane(2).last_bundle is right
            assert board.pane(1).waterfall_pane.history_rows == 1
            assert board.pane(2).waterfall_pane.history_rows == 1
        assert session.accept_frame(first, "caller:rx1", right) == ()
        # Operational route ID and a forged producer ID cannot borrow RX1.
        for source_id in (source, "foreign"):
            forged_frame = replace(left.spectrum, source_id=source_id)
            # Isolate the route guard: an actual density still carrying the
            # original source would rightly refuse even earlier in the bundle.
            forged = replace(left, spectrum=forged_frame, identity=None, persistence=None, waterfall_line=None)
            assert session.accept_frame(first, "caller:rx1", forged) == ()
        metrics = app.paired_performance()
        deadline = time.monotonic() + 2
        while metrics is None:
            assert time.monotonic() < deadline, "bounded asynchronous paired metric deadline"
            time.sleep(.005)
            metrics = app.paired_performance()
        assert metrics is not None and metrics.primary.source_id == "caller:rx1"
        assert metrics.secondary.source_id == "caller:rx2" and metrics.iq_samples_received > 0
        session.stop_resource("physical")
        assert session.admitted_producer_source_id("physical", "caller:rx1") is None
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert leases.active_resource_count == 0 and service.poll_frames() == []
        assert session.accept_frame(first, "caller:rx1", left) == ()
        try:
            preparer.prepare(deliveries[0])
        except ValueError:
            pass
        else:
            raise AssertionError("stopped receipt must refuse retained preparation")
        session.rearm_resource("physical")
        second = session.start_resource("physical")
        assert second is not first
        assert service.latest_snapshot().acquisition_epoch > epoch
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 1
        assert session.accept_frame(second, "caller:rx1", left) == ()
        deadline = time.monotonic() + 10
        while not (fresh := session.poll_resource("physical")):
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert tuple(item.pane_id for item in fresh) == ("left", "right")
        assert all(item.bundle.acquisition_epoch > epoch for item in fresh)
        fresh_prepared = tuple(preparer.prepare(item) for item in fresh)
        assert all(item.bundle.paired_capture is not None for item in fresh_prepared)
        if board is not None:
            # A real native rearm changes actual acquisition/host activation,
            # even when the native pair's local sync counter starts over.
            assert board.apply_prepared(fresh_prepared[0])
            assert board.pane(1).last_bundle is fresh[0].bundle
            assert board.pane(1).waterfall_pane.history_rows == 1
            # The other visible RX clears BEFORE its fresh packet is dequeued.
            assert board.pane(2).last_bundle is None
            assert board.pane(2).spectrum_scene.latest_frame is None
            assert board.pane(2)._last_persistence is None
            assert board.pane(2).waterfall_pane.history_rows == 0
            for stale in prepared:
                assert not board.apply_prepared(stale)
            assert board.pane(2).last_bundle is None
            assert board.pane(2).waterfall_pane.history_rows == 0
            assert board.apply_prepared(fresh_prepared[1])
            qt_app.processEvents()
            assert board.pane(2).last_bundle is fresh[1].bundle
            assert board.pane(2).waterfall_pane.history_rows == 1
            assert not board.apply_prepared(fresh_prepared[0])
            assert not board.apply_prepared(fresh_prepared[1])
        assert session.stop_all() == ()
    finally:
        if session is not None:
            assert session.stop_all() == ()
        app.shutdown()
        if board is not None:
            board.release_presentation_after_shutdown()
            board.close()
            qt_app.processEvents()
        if preparer is not None:
            preparer.clear()
        if settings_directory is not None:
            settings_directory.cleanup()
        # Check terminal ownership even when the visible-state assertion fails.
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        assert service._poller is service._engine is None and leases.active_resource_count == 0
    print(f"same native Live/V2 paired pane {case} PASS; MOCK/offscreen only; no physical RF/Windows acceptance")


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit native module and built Mock IIO")
class NativePairedPaneTests(unittest.TestCase):
    def run_case(self, case: str):
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        environment["SDR_MOCK_LIBIIO_REFILL_DELAY_MS"] = "1"
        environment["QT_QPA_PLATFORM"] = "offscreen"
        if case == "single-layout":
            environment.pop("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", None)
        if case == "empty-serial":
            environment["SDR_MOCK_LIBIIO_EMPTY_SERIAL"] = "1"
        command = "from tests.test_app07_paired_pane_owner import run_native_case; import sys; run_native_case(sys.argv[1],sys.argv[2])"
        result = subprocess.run([sys.executable, "-c", command, MODULE, case], cwd=ROOT,
                                env=environment, capture_output=True, text=True, timeout=45, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_pair_delivers_through_same_v2_graph_and_resource_lease(self):
        self.run_case("delivery")

    def test_actual_native_rearm_clears_both_visible_qt_histories_before_second_packet(self):
        self.run_case("qt-rearm")

    def test_missing_dual_topology_refuses_before_rf_or_lease(self):
        self.run_case("single-layout")

    def test_empty_serial_route_guard_cannot_admit_a_paired_resource(self):
        self.run_case("empty-serial")
