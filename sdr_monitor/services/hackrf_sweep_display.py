"""Explicit, identity-bound HackRF Sweep display owner.

Only reduced native progress/terminal publications cross this boundary.  It
does not expose raw IQ, infer hardware readback, retry activation, or fall back
to another backend.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any, cast

from ..domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceFamily
from ..domain.hackrf_sweep import HackrfSweepRequest
from ..domain.live import LiveAdmissionRejected
from .hackrf_activation_preflight import HackrfRuntimeIdentityProbe, _identity_key
from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID, HackrfBoardKind
from .hackrf_sweep_contract import hackrf_sweep_contract_version
from .completed_line_rate import CompletedLineRateObservation
from .libhackrf_runtime_identity import LibhackrfRuntimeIdentityPort
from .native_continuous_sweep import _to_domain_line, _to_domain_progress
from .readonly_observation_owner import RetainedReadOnlyObserver
from .source_capability_admission import admit_source_request
from .source_capability_providers import _qualified_hackrf_sdk_directory
from .sweep_geometry_contract import require_extended_sweep_geometry


class HackrfSweepDisplayService:
    """One bounded Sweep control, retained across every uncertain cleanup."""

    def __init__(self, native: object, catalog: object, exclusion: object,
                 identity_port_factory: Callable[[], object], manifest: dict[str, object]) -> None:
        self._native, self._catalog, self._exclusion = cast(Any, native), cast(Any, catalog), exclusion
        self._identity: Any = RetainedReadOnlyObserver(cast(Any, identity_port_factory))
        self._manifest = dict(manifest)
        self._lock = threading.RLock()
        self._selection: AnalyzerSourceSelection | None = None
        self._control: Any | None = None
        self._claimed = False
        self._stop_required = False
        self._stopped = False
        self._closed = False
        self._request: HackrfSweepRequest | None = None
        self._last_progress: tuple[int, int] | None = None
        self._last_line = -1
        self._completed_rate = CompletedLineRateObservation()
        self._last_snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())

    def bind_selection(self, selection: AnalyzerSourceSelection) -> None:
        with self._lock:
            if self._claimed or self._stop_required:
                raise RuntimeError("Stop HackRF Sweep before changing source")
            self._selection = selection

    def preflight(self, request: HackrfSweepRequest, selection: AnalyzerSourceSelection) -> None:
        """Reuse Start's pure retained-fact gates without disturbing a live owner.

        No identity enumeration, RF setter, native control creation, claim or
        epoch assignment. Lifecycle and SDK identity admission remain in Start.
        """
        with self._lock:
            self._preflight(request, selection)

    def _preflight(self, request: HackrfSweepRequest, selection: AnalyzerSourceSelection) -> None:
        if (not isinstance(request, HackrfSweepRequest)
                or selection.release_pending or selection.selected is not request.source
                or selection.revision != request.selection_revision
                or request.source.family is not DeviceFamily.HACKRF):
            raise LiveAdmissionRejected("HackRF Sweep source selection is stale or unavailable")
        try:
            request.__post_init__()
        except (TypeError, ValueError):
            raise LiveAdmissionRejected("HackRF Sweep geometry is not admitted") from None
        choice = request.source
        binding = choice.binding
        inventory = self._catalog.snapshot()
        runtime = inventory.runtime_for_adapter(binding.adapter_id)
        if (binding.snapshot is None or binding.calibration_identity is None
                or inventory.binding_for_source(choice.device_id) is not binding
                or runtime is not choice.runtime or runtime is None
                or runtime.availability.value != "available"
                or runtime.adapter_id != HACKRF_LIBHACKRF_ADAPTER_ID
                or binding.adapter_id != HACKRF_LIBHACKRF_ADAPTER_ID):
            raise LiveAdmissionRejected("HackRF Sweep catalog binding is not current")
        try:
            contract = hackrf_sweep_contract_version(self._native, self._manifest)
        except ValueError:
            raise LiveAdmissionRejected("HackRF optional Sweep contract changed") from None
        if contract != 1:
            raise LiveAdmissionRejected("HackRF optional Sweep contract is unavailable")
        if request.requires_extended_geometry:
            try:
                require_extended_sweep_geometry(self._native)
            except ValueError:
                raise LiveAdmissionRejected("HackRF extended Sweep geometry is unavailable") from None
        admitted = admit_source_request(inventory, choice.device_id, "sweep", request,
                                        hackrf_sweep_runtime_available=True)
        if not admitted.accepted:
            raise LiveAdmissionRejected("HackRF Sweep request is not admitted")

    def start(self, request: HackrfSweepRequest, selection: AnalyzerSourceSelection) -> None:
        with self._lock:
            if self._closed or self._claimed or self._stop_required:
                raise RuntimeError("HackRF Sweep requires an open, idle owner")
            self._preflight(request, selection)
            binding = request.source.binding
            assert binding.calibration_identity is not None  # Established by the same pure preflight.
            # Capture the immutable current selection only after all retained
            # catalog and request checks have succeeded.
            self._selection = selection
            if request.epoch < 1 or request.epoch > (1 << 64) - 1:
                raise LiveAdmissionRejected("HackRF Sweep requires an assigned positive epoch")
            observed = self._identity.observe()
            if (not isinstance(observed, HackrfRuntimeIdentityProbe)
                    or observed.board_kind is not HackrfBoardKind.HACKRF_ONE
                    or _identity_key(observed) != binding.calibration_identity.device_identity_key):
                raise LiveAdmissionRejected("HackRF identity preflight refused")
            claim = getattr(self._exclusion, "claim_external_analyzer_rx", None)
            if not callable(claim):
                raise LiveAdmissionRejected("HackRF analyzer RX exclusion is unavailable")
            token = object()
            claim(token)
            self._token = token
            self._claimed = self._stop_required = True
            self._request = request
            # The previous stopped control may still be retained for a final
            # terminal poll. It can NEVER serve as cleanup authority for this
            # new claim if the new factory fails before returning a handle.
            self._control = None
            try:
                control = self._native.create_hackrf_sweep_runtime_control(
                    observed.serial_words, str(request.source.device_id), request.epoch,
                    request.fft_size, request.start_hz // 1_000_000,
                    request.stop_hz // 1_000_000, request.lna_gain, request.vga_gain)
                if not all(callable(getattr(control, name, None)) for name in
                           ("poll_next_publication", "metrics", "stop")):
                    self._control = control  # uncertain effect: retain, never guess cleanup.
                    raise RuntimeError("native Sweep control contract refused")
                self._control = control
                self._stopped = False
                self._last_progress, self._last_line = None, -1
                self._completed_rate.reset()
                self._last_snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())
            except Exception:  # noqa: BLE001 - factory may have partially effected; retain same owner obligation.
                # A throwing factory may have partially effected; explicit Stop
                # remains required and the analyzer exclusion remains held.
                raise RuntimeError("HackRF Sweep activation failed; explicit Stop required") from None

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        with self._lock:
            if self._closed:
                raise RuntimeError("HackRF Sweep display is closed")
            control, request = self._control, self._request
            if control is None or request is None:
                return self._last_snapshot
            try:
                item = control.poll_next_publication()
                line = progress = None
                if item is not None:
                    source, epoch, unit = (getattr(item, "source_id", None),
                                           getattr(item, "epoch", None), getattr(item, "unit", None))
                    if (source != request.source.device_id or epoch != request.epoch
                            or unit != "dBFS/bin"):
                        raise ValueError("foreign Sweep publication identity")
                    if hasattr(item, "revision"):
                        seq = getattr(item, "line_sequence", getattr(item, "sequence", None))
                        rev = item.revision
                        if type(seq) is not int or type(rev) is not int or seq < 0 or rev < 1:
                            raise ValueError("invalid Sweep progress sequence")
                        if self._last_progress is None or (seq, rev) > self._last_progress:
                            acquired = tuple(item.acquired_segment_generations)
                            if any(type(pair) is not tuple or len(pair) != 2 or pair[1] != request.epoch
                                   for pair in acquired):
                                raise ValueError("Sweep progress generation mismatch")
                            progress = _to_domain_progress(item)
                            self._last_progress = (seq, rev)
                    elif hasattr(item, "completed_ns") or hasattr(item, "completed_at_ns"):
                        seq = getattr(item, "line_sequence", getattr(item, "sequence", None))
                        if type(seq) is not int or seq < 0:
                            raise ValueError("invalid Sweep terminal sequence")
                        if seq > self._last_line:
                            generations = tuple(getattr(item, "segment_config_generations", ()))
                            if any(type(pair) is not tuple or len(pair) != 2 or pair[1] != request.epoch
                                   for pair in generations):
                                raise ValueError("Sweep terminal generation mismatch")
                            line = _to_domain_line(item)
                            self._last_line = seq
                    else:
                        raise ValueError("unknown Sweep publication type")
                metrics_raw = control.metrics()
                if not isinstance(metrics_raw, dict):
                    raise TypeError("native Sweep metrics contract mismatch")
                required = {"worker_failed", "terminal_superseded", "progress_superseded",
                            "progress_pending", "terminal_pending", "completed_lines", "gapped_lines"}
                if not required.issubset(metrics_raw):
                    raise ValueError("native Sweep metrics contract is incomplete")
                completed = int(metrics_raw["completed_lines"])
                gapped = int(metrics_raw["gapped_lines"])
                if completed < 0 or gapped < 0:
                    raise ValueError("native Sweep line counters are invalid")
                now_s = time.monotonic()
                rate = self._completed_rate.observe(completed, now_s)
                failed = bool(metrics_raw.get("worker_failed", False))
                metrics = ContinuousSweepDisplayMetrics(
                    completed_line_lps=rate, completed_lines=completed, gapped_lines=gapped,
                    native_line_relay_superseded=max(0, int(metrics_raw["terminal_superseded"])),
                    native_output_superseded=max(0, int(metrics_raw["terminal_superseded"])
                                                   + int(metrics_raw["progress_superseded"])),
                    native_queue_depth=max(0, int(metrics_raw.get("progress_pending", 0))
                                           + int(metrics_raw.get("terminal_pending", 0))),
                    native_queue_capacity=2, has_error=failed,
                    acquisition_finished=self._stopped,
                    error="HackRF Sweep worker failed; explicit Stop required" if failed else None)
                self._last_snapshot = ContinuousSweepDisplaySnapshot(line, metrics, progress)
                return self._last_snapshot
            except Exception:  # noqa: BLE001 - quarantine malformed native payload; retain control for Stop.
                raise RuntimeError("HackRF Sweep publication contract failed closed; explicit Stop required") from None

    def stop(self) -> None:
        with self._lock:
            if not self._stop_required:
                # An enumeration-only preflight can fail with a retained
                # observer even though no RX claim/factory was ever reached.
                # Explicit Stop must resolve that same owner too.
                if self._identity.cleanup_pending:
                    try:
                        self._identity.close()
                    except Exception:
                        raise RuntimeError("HackRF identity release failed; owner retained") from None
                return
            control = self._control
            if control is None:
                raise RuntimeError("HackRF Sweep owner is unresolved; explicit Stop required")
            try:
                result = control.stop(5000)
                if not isinstance(result, dict) or result.get("complete") is not True:
                    raise RuntimeError
                self._stopped = True
            except Exception:  # noqa: BLE001 - only confirmed completion waives explicit cleanup obligation.
                raise RuntimeError("HackRF Sweep Stop was not confirmed; owner retained") from None
            release = getattr(self._exclusion, "release_external_analyzer_rx", None)
            try:
                if not callable(release):
                    raise TypeError("release operation unavailable")
                release(self._token)
            except Exception:  # noqa: BLE001 - keep analyzer exclusion obligation on any release failure.
                raise RuntimeError("HackRF analyzer RX release failed; owner retained") from None
            self._claimed = self._stop_required = False
            # Keep the stopped control so poll_latest can drain terminal output.

    def close(self) -> None:
        with self._lock:
            if self._stop_required:
                self.stop()
            if self._identity.cleanup_pending:
                self._identity.close()
            self._closed = True


def build_hackrf_sweep_display_service(native_live: Any, catalog: Any) -> HackrfSweepDisplayService | None:
    """Compose only from the already-loaded, manifest-qualified native module."""
    native = getattr(native_live, "_native", None)
    directory = _qualified_hackrf_sdk_directory(native)
    if directory is None:
        return None
    try:
        manifest_path = directory / "native_build_manifest.json"
        with manifest_path.open("rb") as stream:
            encoded = stream.read(16_385)
        if len(encoded) > 16_384:
            return None
        manifest = json.loads(encoded)
        module_path = getattr(native, "__file__", None)
        if not isinstance(manifest, dict) or not isinstance(module_path, str):
            return None
        with open(module_path, "rb") as stream:
            module_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if (module_hash != manifest.get("artifact_sha256")
                or manifest.get("hackrf_official_compiled") is not True
                or manifest.get("hackrf_factory_contract_version") != 2
                or hackrf_sweep_contract_version(native, manifest) != 1):
            return None
        return HackrfSweepDisplayService(native, catalog, native_live,
            lambda: LibhackrfRuntimeIdentityPort(directory / "hackrf.dll", directory), manifest)
    except (OSError, TypeError, ValueError):
        return None


__all__ = ["HackrfSweepDisplayService", "build_hackrf_sweep_display_service"]
