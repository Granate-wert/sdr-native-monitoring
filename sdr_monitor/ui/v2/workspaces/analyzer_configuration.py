"""Draft-only configuration drawer for the shared Analyzer canvas."""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Callable, TypedDict

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.live_configuration_patch import LiveConfigurationPatch

from ..design import ThemeId, stylesheet_for_theme
from ..i18n import text
from ..state.configuration_readouts import configuration_prefix, rf_bandwidth_summary
from ..view_models.analyzer_view_model import AnalyzerViewModel, AnalyzerViewState, AnalyzerMode
from .analyzer_sweep_profile import AnalyzerSweepProfileControl


class _DraftChanges(TypedDict, total=False):
    center_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float | None
    gain_db: float
    fft_size: int
    backend: BackendKind


class AnalyzerConfigurationDrawer(QFrame):
    """A local draft editor; only explicit Use/Apply delegate a public command."""

    close_requested = Signal()
    draft_changed = Signal()

    def __init__(self, model: AnalyzerViewModel, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._theme = ThemeId.DARK
        self._dirty = False
        self._conflicted = False
        self._pending: tuple[int, LiveConfiguration] | None = None
        self._base_identity: tuple[str, int, str] | None = None
        self._has_applied = False
        self._backend_selectable = False
        self._published_backends: tuple[BackendKind, ...] = ()
        self._bandwidth_selectable = False
        self._published_bandwidths: tuple[float, ...] = ()
        self._bandwidth_choices: tuple[float | None, ...] = ()
        # Before the first immutable applied snapshot, an explicit Apply may
        # submit only a normal LiveConfiguration built from public defaults.
        self._base = LiveConfiguration()
        self._labels: list[tuple[QLabel | QPushButton, str]] = []
        self._build()
        self._load(self._base)
        self._unsubscribe: Callable[[], None] | None = model.subscribe(self._render)
        self.set_locale()

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def pending(self) -> bool:
        """True only after an Apply request awaits immutable confirmation."""
        return self._pending is not None

    @property
    def can_apply(self) -> bool:
        return self._apply.isEnabled()

    @property
    def can_cancel(self) -> bool:
        return self._cancel.isEnabled()

    def apply_draft(self) -> None:
        """One explicit transaction shared by main-bar and drawer buttons."""
        self._apply.click()

    def cancel_draft(self) -> None:
        """Cancel only local edits through the same validated draft owner."""
        self._cancel.click()

    def preview_configuration(self) -> LiveConfiguration:
        """Immutable local draft, not applied readback or an Apply command."""
        if self._conflicted or self.pending:
            raise ValueError(text("live.configuration.conflict") if self._conflicted
                             else text("live.configuration.requested"))
        return replace(self._base, **self._draft_changes())

    def take_quick_controls(self) -> tuple[QDoubleSpinBox, QSpinBox, QDoubleSpinBox]:
        """Move existing editors to the main bar, never create a second draft."""
        for field in (self._center, self._fft, self._gain):
            if self._measurement_form.labelForField(field) is not None:
                row = self._measurement_form.takeRow(field)
                label = row.labelItem.widget()
                if label is not None:
                    label.setParent(self)
                    label.hide()
                field.setParent(None)
        return self._center, self._fft, self._gain

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))

    def preferred_height(self, width: int) -> int:
        """Fit wrapped content, not QScrollArea's cached/capped size hint.

        The workspace clamps this to its available height; small screens keep
        normal scroll and keyboard reachability instead of hiding controls.
        """
        layout = self._content_layout
        inner_width = max(1, width - 2 * self.frameWidth())
        height = (layout.totalHeightForWidth(inner_width) if layout.hasHeightForWidth()
                  else layout.sizeHint().height())
        return max(height, layout.minimumSize().height()) + 2 * self.frameWidth()

    def set_locale(self, _locale: object | None = None) -> None:
        """Retranslate in place; no widget or draft is recreated."""
        self.setAccessibleName(text("analyzer.settings"))
        for widget, key in self._labels:
            widget.setText(text(key))
            widget.setAccessibleName(text(key))
        self._uri.setPlaceholderText(text("live.uri.placeholder"))
        self._uri.setAccessibleName(text("live.uri.label"))
        self.sweep_profile.set_locale()
        self._translate_bandwidth_options()
        self._rf_bandwidth.setAccessibleName(text("analyzer.rf_bandwidth"))
        self._rf_bandwidth.setAccessibleDescription(text("analyzer.rf_bandwidth.help"))
        self._rf_bandwidth.setToolTip(text("analyzer.rf_bandwidth.help"))
        if self._uri_error.isVisible():
            self._uri_error.setText(text("live.uri.error.transport"))
        self._render_status()

    def dispose(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def closeEvent(self, event) -> None:
        self.dispose()
        super().closeEvent(event)

    def _build(self) -> None:
        self.setProperty("ui2Role", "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setProperty("ui2Role", "panel-scroll")
        self.scroll_area.viewport().setProperty("ui2Role", "panel-scroll-content")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        contents = QWidget(self.scroll_area)
        contents.setProperty("ui2Role", "panel-scroll-content")
        outer = QVBoxLayout(contents)
        self._content_layout = outer
        self.scroll_area.setWidget(contents)
        shell.addWidget(self.scroll_area)
        heading = QHBoxLayout()
        title = QLabel(self)
        title.setProperty("ui2Role", "section-heading")
        self._labels.append((title, "analyzer.settings"))
        heading.addWidget(title)
        heading.addStretch(1)
        self._close = QPushButton(text("analyzer.close"), self)
        self._close.setProperty("ui2Role", "utility-action")
        self._close.clicked.connect(self.close_requested.emit)
        self._labels.append((self._close, "analyzer.close"))
        heading.addWidget(self._close)
        outer.addLayout(heading)
        uri_row = QHBoxLayout()
        self._uri = QLineEdit(self)
        self._uri.setProperty("ui2Role", "command-field")
        self._uri.setPlaceholderText(text("live.uri.placeholder"))
        self._uri.setAccessibleName(text("live.uri.label"))
        self._use_uri = QPushButton(self)
        self._use_uri.clicked.connect(self._on_use_uri)
        self._use_uri.setProperty("ui2Role", "utility-action")
        self._labels.append((self._use_uri, "live.uri.use"))
        uri_row.addWidget(self._uri, 1)
        uri_row.addWidget(self._use_uri)
        outer.addLayout(uri_row)
        self._uri_error = QLabel(self)
        self._uri_error.setProperty("ui2Tone", "error")
        self._uri_error.hide()
        outer.addWidget(self._uri_error)
        form = QFormLayout()
        self._measurement_form = form
        self._center = self._float(0.001, 10_000.0, 3)
        self._sample_rate = self._float(0.001, 100.0, 3)
        self._rf_bandwidth = QComboBox(self)
        self._rf_bandwidth.setProperty("ui2Role", "utility-select")
        self._rf_bandwidth.currentIndexChanged.connect(self._edited)
        self._gain = self._float(-100.0, 100.0, 1)
        self._fft = QSpinBox(self)
        self._fft.setProperty("ui2Role", "range-control")
        self._fft.setRange(256, 262_144)
        self._fft.setSingleStep(256)
        self._fft.valueChanged.connect(self._edited)
        self._backend = QComboBox(self)
        self._backend.setProperty("ui2Role", "utility-select")
        self._backend.currentIndexChanged.connect(self._edited)
        for key, field in (("analyzer.center", self._center), ("analyzer.sample_rate", self._sample_rate),
                           ("analyzer.rf_bandwidth", self._rf_bandwidth), ("analyzer.gain", self._gain),
                           ("analyzer.fft", self._fft), ("live.backend.name", self._backend)):
            label = QLabel(self)
            label.setProperty("ui2Role", "secondary")
            label.setBuddy(field)
            self._labels.append((label, key))
            form.addRow(label, field)
        outer.addLayout(form)
        self.sweep_profile = AnalyzerSweepProfileControl(self)
        outer.addWidget(self.sweep_profile)
        self._applied = QLabel(self)
        self._applied.setProperty("ui2Role", "secondary")
        self._applied.setWordWrap(True)
        outer.addWidget(self._applied)
        self._status = QLabel(self)
        self._status.setProperty("ui2Role", "secondary")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)
        buttons = QHBoxLayout()
        self._apply = QPushButton(self)
        self._apply.clicked.connect(self._on_apply)
        self._apply.setProperty("ui2Role", "primary-action")
        self._cancel = QPushButton(self)
        self._cancel.clicked.connect(self._cancel_draft)
        self._cancel.setProperty("ui2Role", "utility-action")
        self._labels.extend(((self._apply, "analyzer.apply"), (self._cancel, "analyzer.cancel")))
        buttons.addWidget(self._apply)
        buttons.addWidget(self._cancel)
        outer.addLayout(buttons)

    def _float(self, low: float, high: float, decimals: int) -> QDoubleSpinBox:
        field = QDoubleSpinBox(self)
        field.setProperty("ui2Role", "range-control")
        field.setRange(low, high)
        field.setDecimals(decimals)
        field.valueChanged.connect(self._edited)
        return field

    def _on_use_uri(self) -> None:
        uri = self._uri.text().strip()
        if not (uri.startswith("usb:") or uri.startswith("ip:")) or any(char.isspace() for char in uri):
            self._uri_error.setText(text("live.uri.error.transport"))
            self._uri_error.show()
            return
        if self._model.select_manual_uri(uri):
            self._uri_error.hide()

    def _edited(self, _value: object) -> None:
        if self._pending is not None:
            return
        self._dirty = self._draft_changes() != {}
        locked = self._model.state.controls_locked
        self._apply.setEnabled(self._can_submit())
        self._cancel.setEnabled(not locked and self._dirty)
        self._render_status()
        self.draft_changed.emit()

    def _on_apply(self) -> None:
        if not self._can_submit():
            return
        changes = self._draft_changes()
        snapshot = self._model.state.live.snapshot
        identity = _identity(snapshot)
        if self._base_identity is None or identity is None:
            self._status.setText(text("live.configuration.identity_missing"))
            return
        if self._base_identity is not None and identity != self._base_identity:
            self._conflicted = True
            self._render_status()
            self.draft_changed.emit()
            return
        try:
            payload: LiveConfiguration | LiveConfigurationPatch
            if not self._has_applied:
                payload = replace(self._base, **changes)
            else:
                session_id, generation, source_id = self._base_identity
                payload = LiveConfigurationPatch(session_id, generation, source_id, tuple(changes.items()))
            expected = replace(self._base, **changes)
            # Latch before dispatch: a fast public confirmation may be emitted
            # synchronously by a fake/in-memory adapter.
            self._pending = (identity[1], expected)
            self._render_status()
            if not self._model.apply_configuration(payload):
                self._pending = None
                self._resolve_model_pending()
                self._render_status()
                return
        except Exception as error:
            self._pending = None
            self._resolve_model_pending()
            self._status.setText(str(error))
            return
        else:
            self._render_status()
            self.draft_changed.emit()

    def _cancel_draft(self) -> None:
        snapshot = self._model.state.live.snapshot
        current = getattr(getattr(snapshot, "applied", None), "applied", None)
        identity = _identity(snapshot)
        if isinstance(current, LiveConfiguration) and identity is not None:
            self._base, self._base_identity = current, identity
            self._has_applied = True
        elif identity is not None and not _same_owner(identity, self._base_identity):
            self._base, self._base_identity = LiveConfiguration(), identity
            self._has_applied = False
        self._load(self._base)
        self._dirty = self._conflicted = False
        self._pending = None
        self._resolve_model_pending()
        # Recompute action availability before notifying the main frequency
        # bar; restoring values alone left Apply/Cancel enabled for a clean draft.
        self._edited(None)

    def _render(self, state: AnalyzerViewState) -> None:
        snapshot = state.live.snapshot
        configuration = getattr(getattr(snapshot, "applied", None), "applied", None)
        identity = _identity(snapshot)
        state_changed = False
        new_owner = identity is not None and self._base_identity is not None and not _same_owner(
            identity, self._base_identity)
        if new_owner:
            # A different source/session is a new configuration authority.
            # Never carry a patch base or an in-flight request across it.
            self._base = configuration if isinstance(configuration, LiveConfiguration) else LiveConfiguration()
            self._base_identity = identity
            self._has_applied = isinstance(configuration, LiveConfiguration)
            self._pending = None
            self._resolve_model_pending()
            self._dirty = self._conflicted = False
            state_changed = True
        # Rebuild source-scoped choices only after discarding the previous
        # owner's base. Otherwise its applied RF value leaks into a new source.
        self._sync_backend_options(snapshot, configuration)
        self._sync_bandwidth_options(snapshot, configuration)
        if new_owner:
            self._load(self._base)
        if isinstance(configuration, LiveConfiguration):
            self._has_applied = True
            if (
                self._pending is not None and identity is not None and self._base_identity is not None
                and identity[0] == self._base_identity[0] and identity[2] == self._base_identity[2]
                and identity[1] > self._pending[0]
            ):
                if not state.live.error_label:
                    # Backend readback is authoritative; normalization and
                    # capability clamping are successful confirmations too.
                    self._base, self._base_identity = configuration, identity
                    self._load(configuration)
                    self._dirty = False
                    self._conflicted = False
                    self._pending = None
                    self._resolve_model_pending()
                    state_changed = True
            elif self._dirty and self._base_identity is not None and (
                identity != self._base_identity or configuration != self._base
            ):
                if not self._conflicted:
                    self._conflicted = True
                    state_changed = True
            elif not self._dirty and (
                configuration != self._base or identity != self._base_identity
            ):
                self._base, self._base_identity = configuration, identity
                self._load(configuration)
        if self._pending is not None and state.live.error_label:
            # A rejected request need not advance configuration generation.
            # Preserve the draft for correction, but release pending controls.
            self._pending = None
            self._resolve_model_pending()
            state_changed = True
        locked = state.controls_locked
        if configuration is None and identity is not None and self._base_identity is None:
            self._base_identity = identity
        editing_locked = locked or self._pending is not None
        self.sweep_profile.setVisible(state.mode is AnalyzerMode.SWEEP)
        self.sweep_profile.setEnabled(not editing_locked)
        for field in (self._uri, self._use_uri, self._center, self._sample_rate, self._gain, self._fft):
            field.setEnabled(not editing_locked)
        self._backend.setEnabled(not editing_locked and self._backend_selectable)
        self._rf_bandwidth.setEnabled(not editing_locked and self._bandwidth_selectable)
        self._apply.setEnabled(self._can_submit())
        self._cancel.setEnabled(not locked and self._dirty)
        self._applied.setText(text("live.configuration.no_applied") if configuration is None else
                              configuration_prefix(getattr(snapshot, "applied", None)) + " " +
                              _configuration_summary(configuration))
        self._render_status()
        if state_changed:
            self.draft_changed.emit()

    def _resolve_model_pending(self) -> None:
        resolve = getattr(self._model, "resolve_configuration_request", None)
        if callable(resolve):
            resolve()

    def _load(self, configuration: LiveConfiguration) -> None:
        for field, value in ((self._center, configuration.center_hz / 1e6), (self._sample_rate, configuration.sample_rate_hz / 1e6), (self._gain, configuration.gain_db)):
            with QSignalBlocker(field):
                field.setValue(value)
        with QSignalBlocker(self._fft):
            self._fft.setValue(configuration.fft_size)
        bandwidth_index = self._rf_bandwidth.findData(configuration.analog_bandwidth_hz)
        if bandwidth_index >= 0:
            with QSignalBlocker(self._rf_bandwidth):
                self._rf_bandwidth.setCurrentIndex(bandwidth_index)
        backend_index = self._backend.findData(configuration.backend.value)
        if backend_index >= 0:
            with QSignalBlocker(self._backend):
                self._backend.setCurrentIndex(backend_index)

    def _draft_changes(self) -> _DraftChanges:
        backend_value = self._backend.currentData()
        backend = BackendKind(str(backend_value)) if isinstance(backend_value, str) else self._base.backend
        changes: _DraftChanges = {}
        center = self._center.value() * 1e6
        rate = self._sample_rate.value() * 1e6
        gain = self._gain.value()
        if abs(center - self._base.center_hz) > 500.0:
            changes["center_hz"] = center
        if abs(rate - self._base.sample_rate_hz) > 500.0:
            changes["sample_rate_hz"] = rate
        if self._rf_bandwidth.count() and self._rf_bandwidth.currentData() != self._base.analog_bandwidth_hz:
            changes["analog_bandwidth_hz"] = self._rf_bandwidth.currentData()
        if abs(gain - self._base.gain_db) > 0.05:
            changes["gain_db"] = gain
        if self._fft.value() != self._base.fft_size:
            changes["fft_size"] = self._fft.value()
        if backend != self._base.backend:
            changes["backend"] = backend
        return changes

    def _sync_backend_options(self, snapshot: object | None, configuration: object | None) -> None:
        """Render only backend choices published by the selected device.

        A source identity proves neither backend support nor permission to use a
        backend.  Until capabilities say otherwise we deliberately leave the
        selector disabled instead of expanding it to every BackendKind.
        """
        capabilities = getattr(getattr(snapshot, "device", None), "capabilities", None)
        available: list[BackendKind] = []
        for value in getattr(capabilities, "supported_backends", ()):
            try:
                backend = value if isinstance(value, BackendKind) else BackendKind(str(value))
            except ValueError:
                continue
            if backend not in available:
                available.append(backend)
        published = tuple(available)
        current = configuration.backend if isinstance(configuration, LiveConfiguration) else self._base.backend
        retain_unpublished = current not in published
        choices = ((current,) if retain_unpublished else ()) + published
        if choices == self._published_backends and self._backend.count() == len(choices):
            self._backend_selectable = bool(published) and not retain_unpublished
            return
        self._published_backends = choices
        with QSignalBlocker(self._backend):
            self._backend.clear()
            for backend in choices:
                self._backend.addItem(backend.value.upper(), backend.value)
        self._backend_selectable = bool(published) and not retain_unpublished

    def _sync_bandwidth_options(self, snapshot: object | None, configuration: object | None) -> None:
        """Offer adapter presets, retaining unknown applied readback without inventing choices."""
        capabilities = getattr(getattr(snapshot, "device", None), "capabilities", None)
        published = tuple(sorted({float(value) for value in getattr(capabilities, "analog_bandwidths_hz", ())
                                  if not isinstance(value, bool) and isinstance(value, (int, float))
                                  and isfinite(value) and value > 0.0}))
        current = (configuration.analog_bandwidth_hz if isinstance(configuration, LiveConfiguration)
                   else self._base.analog_bandwidth_hz)
        choices: tuple[float | None, ...] = ((None, current) if current is not None and current not in published
                                             else (None,)) + published
        self._bandwidth_selectable = bool(published)
        if choices == self._bandwidth_choices and self._rf_bandwidth.count() == len(choices):
            return
        selected = self._rf_bandwidth.currentData() if self._rf_bandwidth.count() else current
        if self._dirty and selected not in choices and selected != current:
            self._conflicted = True
        self._published_bandwidths = published
        self._bandwidth_choices = choices
        with QSignalBlocker(self._rf_bandwidth):
            self._rf_bandwidth.clear()
            for value in choices:
                self._rf_bandwidth.addItem("", value)
            index = self._rf_bandwidth.findData(selected)
            self._rf_bandwidth.setCurrentIndex(index if index >= 0 else self._rf_bandwidth.findData(current))
        self._translate_bandwidth_options()

    def _translate_bandwidth_options(self) -> None:
        with QSignalBlocker(self._rf_bandwidth):
            for index, value in enumerate(self._bandwidth_choices):
                if value is None:
                    label = text("analyzer.rf_bandwidth.auto")
                elif value not in self._published_bandwidths:
                    label = text("analyzer.rf_bandwidth.current", value=f"{value / 1e6:g}")
                else:
                    label = f"{value / 1e6:g} MHz"
                self._rf_bandwidth.setItemText(index, label)

    def _can_submit(self) -> bool:
        identity = _identity(self._model.state.live.snapshot)
        return (
            identity is not None and identity == self._base_identity
            and not self._model.state.controls_locked
            and (self._dirty or not self._has_applied)
            and not self._conflicted and self._pending is None
        )

    def _render_status(self) -> None:
        if getattr(self._model.state.live.snapshot, "device", None) is None:
            self._status.setText(text("analyzer.settings.select_source"))
        elif _identity(self._model.state.live.snapshot) is None:
            self._status.setText(text("live.configuration.identity_missing"))
        elif self._conflicted:
            self._status.setText(text("live.configuration.conflict"))
        elif self._pending is not None:
            self._status.setText(text("live.configuration.requested"))
        elif self._dirty:
            self._status.setText(text("live.configuration.local_changes"))
        elif not self._backend_selectable:
            self._status.setText(text("live_state.backend.empty"))
        else:
            self._status.setText("")


def _identity(snapshot: object | None) -> tuple[str, int, str] | None:
    device = getattr(snapshot, "device", None)
    session_id, generation, source_id = getattr(snapshot, "session_id", None), getattr(snapshot, "generation", None), getattr(device, "device_id", None)
    if isinstance(session_id, str) and session_id and type(generation) is int and generation >= 0 and isinstance(source_id, str) and source_id:
        return session_id, generation, source_id
    return None


def _same_owner(
    left: tuple[str, int, str] | None,
    right: tuple[str, int, str] | None,
) -> bool:
    return left is not None and right is not None and (left[0], left[2]) == (right[0], right[2])


def _configuration_summary(value: LiveConfiguration) -> str:
    return (f"{value.center_hz / 1e6:g} MHz · Fs {value.sample_rate_hz / 1e6:g} MS/s · "
            f"{rf_bandwidth_summary(value.analog_bandwidth_hz)} · FFT {value.fft_size} · "
            f"{value.gain_db:g} dB")
