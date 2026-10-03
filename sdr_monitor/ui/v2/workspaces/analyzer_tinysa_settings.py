"""Model/firmware-aware tinySA draft drawer; no device or session ownership."""

from collections.abc import Callable

from PySide6.QtCore import QPointF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QStandardItemModel, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain.calibration import CalibrationProfile
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.tinysa_settings import (
    TINYSA_RUNTIME_CONTROL_CONTRACT,
    TinySaAttenuationMode,
    TinySaInputMode,
    TinySaRbwMode,
    TinySaSpurPolicy,
    TinySaSweepAccuracy,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
)

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerViewState
from ..view_models.calibration_view_model import CalibrationProfileViewModel, CalibrationProfileViewState


class DraftScrollComboBox(QComboBox):
    """Let the editor scroll while a closed draft selector is under the wheel."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            ancestor = self.parentWidget()
            while ancestor is not None and not isinstance(ancestor, QScrollArea):
                ancestor = ancestor.parentWidget()
            if isinstance(ancestor, QScrollArea):
                viewport = ancestor.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                )
                QApplication.sendEvent(viewport, forwarded)
                event.accept()
            else:
                event.ignore()


def requested_tinysa_parts(input_mode: TinySaInputMode, plan: TinySaSweepSettingsPlan, *,
                           readback: bool = False, correction: CalibrationProfile | None = None) -> tuple[str, ...]:
    """Localized operator request from typed intent, never serial commands or readback."""
    parts = []
    if input_mode is not TinySaInputMode.PRESERVE:
        parts.append(text("tinysa.settings.requested_input", value=text(f"tinysa.settings.{input_mode.value}")))
    if plan.accuracy is not TinySaSweepAccuracy.UNCHANGED:
        accuracy = "noise" if plan.accuracy is TinySaSweepAccuracy.NOISE_SOURCE else plan.accuracy.value
        parts.append(text("tinysa.settings.requested_accuracy", value=text(f"tinysa.settings.{accuracy}")))
    if plan.rbw_mode is not TinySaRbwMode.UNCHANGED:
        if plan.rbw_mode is TinySaRbwMode.AUTO:
            value = text("tinysa.settings.auto")
        else:
            assert plan.rbw_hz is not None
            value = f"{plan.rbw_hz / 1000:g}{text('tinysa.settings.unit.khz')}"
        parts.append(text("tinysa.settings.requested_rbw", value=value))
    if plan.attenuation_mode is not TinySaAttenuationMode.UNCHANGED:
        value = (text("tinysa.settings.auto") if plan.attenuation_mode is TinySaAttenuationMode.AUTO else
                 f"{plan.attenuation_db}{text('tinysa_analyzer.unit.db')}")
        parts.append(text("tinysa.settings.requested_atten", value=value))
    for value, unchanged, key in (
            (plan.lna, TinySaSwitchPolicy.UNCHANGED, "tinysa.settings.requested_lna"),
            (plan.spur_removal, TinySaSpurPolicy.UNCHANGED, "tinysa.settings.requested_spur")):
        if value is not unchanged:
            parts.append(text(key, value=text(f"tinysa.settings.{value.value}")))
    if plan.repeat_count is not None:
        parts.append(text("tinysa.settings.requested_repeat", value=plan.repeat_count))
    if plan.sweep_time_ms is not None:
        parts.append(text("tinysa.settings.requested_sweep_time", value=f"{plan.sweep_time_ms / 1000:g}"))
    if readback:
        parts.append(text("tinysa.settings.requested_readback"))
    if correction is not None:
        parts.append(text("tinysa.settings.requested_correction", value=correction.profile_id))
    return tuple(parts)


class TinySaSettingsDrawer(QFrame):
    draft_changed = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, embedded_in_editor: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("v2-tinysa-settings-drawer")
        self.setProperty("ui2Role", "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._source_key: tuple[str, int | None] | None = None
        self._profiles_model: CalibrationProfileViewModel | None = None
        self._unsubscribe_profiles: Callable[[], None] | None = None
        self._profiles_locked = False
        self._pending_profiles: CalibrationProfileViewState | None = None
        self._embedded_in_editor = embedded_in_editor
        self._labels: list[tuple[QLabel | QPushButton, str]] = []
        self._field_labels: list[tuple[QWidget, QLabel, str]] = []
        self._options: list[tuple[QComboBox, tuple[str, ...]]] = []
        if embedded_in_editor:
            # The independent editor supplies the only scroll viewport.
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
        else:
            # Preserve the standalone Analyzer drawer's existing bounded viewport.
            outer = QVBoxLayout(self)
            self.scroll_area = QScrollArea(self)
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll_area.setProperty("ui2Role", "panel-scroll")
            contents = QWidget(self.scroll_area)
            contents.setProperty("ui2Role", "panel-scroll-content")
            layout = QVBoxLayout(contents)
            self.scroll_area.setWidget(contents)
            outer.addWidget(self.scroll_area)
        heading = QHBoxLayout()
        title = QLabel(self)
        title.setProperty("ui2Role", "section-heading")
        self._labels.append((title, "tinysa.settings.title"))
        heading.addWidget(title, 1)
        self.close_button = QPushButton(self)
        self.close_button.setProperty("ui2Role", "utility-action")
        self.close_button.clicked.connect(self.close_requested.emit)
        self._labels.append((self.close_button, "analyzer.close"))
        heading.addWidget(self.close_button)
        layout.addLayout(heading)
        self.contract = QLabel(self)
        self.contract.setProperty("ui2Role", "secondary")
        self.contract.setWordWrap(True)
        layout.addWidget(self.contract)
        self.requested = QLabel(self)
        self.requested.setWordWrap(True)
        self.requested.setTextFormat(Qt.TextFormat.PlainText)
        self.requested.setProperty("ui2Role", "secondary")
        layout.addWidget(self.requested)
        self._form = QFormLayout()
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(self._form)
        self.input = self._combo("tinysa.settings.input", tuple(TinySaInputMode),
                                 ("tinysa.settings.preserve", "tinysa.settings.low", "tinysa.settings.high"))
        self.accuracy = self._combo("tinysa.settings.accuracy", tuple(TinySaSweepAccuracy),
            ("tinysa.settings.preserve", "tinysa.settings.normal", "tinysa.settings.precise",
             "tinysa.settings.fast", "tinysa.settings.noise"))
        self.rbw_mode = self._combo("tinysa.settings.rbw", tuple(TinySaRbwMode),
            ("tinysa.settings.preserve", "tinysa.settings.auto", "tinysa.settings.manual"))
        self.rbw = QDoubleSpinBox(self)
        self.rbw.setRange(.2, 850)
        self.rbw.setDecimals(1)
        self.rbw.setSingleStep(.1)
        self.rbw.setValue(30)
        self._numeric(self.rbw, "tinysa.settings.rbw_target")
        self.attenuation_mode = self._combo("tinysa.settings.atten", tuple(TinySaAttenuationMode),
            ("tinysa.settings.preserve", "tinysa.settings.auto", "tinysa.settings.manual"))
        self.attenuation = QSpinBox(self)
        self.attenuation.setRange(0, 31)
        self.attenuation.setValue(10)
        self._numeric(self.attenuation, "tinysa.settings.atten_value")
        self.lna = self._combo("tinysa.settings.lna", tuple(TinySaSwitchPolicy),
            ("tinysa.settings.preserve", "tinysa.settings.off", "tinysa.settings.on"))
        self.spur = self._combo("tinysa.settings.spur", tuple(TinySaSpurPolicy),
            ("tinysa.settings.preserve", "tinysa.settings.off", "tinysa.settings.on", "tinysa.settings.auto"))
        self.average = QSpinBox(self)
        self.average.setRange(0, 1000)
        self._numeric(self.average, "tinysa.settings.average")
        self.screen_time = QDoubleSpinBox(self)
        self.screen_time.setRange(0, 60)
        self.screen_time.setDecimals(3)
        self._numeric(self.screen_time, "tinysa.settings.screen_time")
        self.correction = DraftScrollComboBox(self)
        self.correction.setProperty("ui2Role", "utility-select")
        self.correction.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.correction.setMinimumContentsLength(10)
        self.correction.addItem("", None)
        self._add_field(self.correction, "tinysa.correction.profile")
        self.correction.currentIndexChanged.connect(self._changed)
        self.frontend_chain = QLineEdit(self)
        self.frontend_chain.setProperty("ui2Role", "command-field")
        self.frontend_chain.setMaxLength(128)
        self._add_field(self.frontend_chain, "tinysa.correction.chain")
        self.frontend_chain.textChanged.connect(self._changed)
        self.extrapolate = QCheckBox(self)
        self.extrapolate.setProperty("ui2Role", "utility-toggle")
        self.extrapolate.toggled.connect(self._changed)
        layout.addWidget(self.extrapolate)
        self.refresh_profiles = QPushButton(self)
        self.refresh_profiles.setProperty("ui2Role", "utility-action")
        self.refresh_profiles.clicked.connect(self._refresh_profiles)
        layout.addWidget(self.refresh_profiles)
        self.correction_scope = QLabel(self)
        self.correction_scope.setProperty("ui2Role", "secondary")
        self.correction_scope.setWordWrap(True)
        self.correction_scope.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.correction_scope)
        self.readback = QCheckBox(self)
        self.readback.setProperty("ui2Role", "utility-toggle")
        self.readback.toggled.connect(self._changed)
        layout.addWidget(self.readback)
        self.scope = QLabel(self)
        self.scope.setProperty("ui2Role", "secondary")
        self.scope.setWordWrap(True)
        self.scope.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.scope)
        layout.addStretch(1)
        self.set_locale()
        self.hide()

    def _combo(self, key: str, values: tuple, keys: tuple[str, ...]) -> QComboBox:
        control = DraftScrollComboBox(self)
        control.setProperty("ui2Role", "utility-select")
        control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        control.setMinimumContentsLength(10)
        for value in values:
            control.addItem("", value.value)
        self._options.append((control, keys))
        self._add_field(control, key)
        control.currentIndexChanged.connect(self._changed)
        return control

    def _add_field(self, control: QWidget, key: str) -> None:
        label = QLabel(self)
        label.setProperty("ui2Role", "secondary")
        label.setWordWrap(True)
        self._labels.append((label, key))
        self._form.addRow(label, control)
        self._field_labels.append((control, label, key))
        control.setAccessibleName(text(key))

    def _numeric(self, control: QDoubleSpinBox | QSpinBox, key: str) -> None:
        control.setProperty("ui2Role", "range-control")
        control.setKeyboardTracking(False)
        self._add_field(control, key)
        control.valueChanged.connect(self._changed)

    def _changed(self, _value: object = None) -> None:
        self._refresh_requested()
        self.draft_changed.emit()

    def requested_count(self) -> int:
        return len(self._requested_parts())

    def _requested_parts(self) -> tuple[str, ...]:
        return requested_tinysa_parts(TinySaInputMode(self.input.currentData()), self.plan(),
                                      readback=self.readback.isChecked(), correction=self.correction_profile())

    def _refresh_requested(self) -> None:
        parts = self._requested_parts()
        value = (text("tinysa.settings.requested", changes=" · ".join(parts)) if parts else
                 text("tinysa.settings.requested_none"))
        self.requested.setText(value + " " + text("tinysa.settings.requested_scope"))
        self.requested.setAccessibleName(self.requested.text())
        if not self._embedded_in_editor:
            return
        plan = self.plan()
        requested_controls: set[QWidget] = set()
        if self.input.currentData() != TinySaInputMode.PRESERVE.value:
            requested_controls.add(self.input)
        if plan.accuracy is not TinySaSweepAccuracy.UNCHANGED:
            requested_controls.add(self.accuracy)
        if plan.rbw_mode is not TinySaRbwMode.UNCHANGED:
            requested_controls.add(self.rbw_mode)
        if plan.rbw_mode is TinySaRbwMode.MANUAL:
            requested_controls.add(self.rbw)
        if plan.attenuation_mode is not TinySaAttenuationMode.UNCHANGED:
            requested_controls.add(self.attenuation_mode)
        if plan.attenuation_mode is TinySaAttenuationMode.MANUAL:
            requested_controls.add(self.attenuation)
        for option, choice in ((self.lna, plan.lna), (self.spur, plan.spur_removal)):
            if choice.value != "unchanged":
                requested_controls.add(option)
        if plan.repeat_count is not None:
            requested_controls.add(self.average)
        if plan.sweep_time_ms is not None:
            requested_controls.add(self.screen_time)
        if self.correction_profile() is not None:
            requested_controls.add(self.correction)
        for control, label, key in self._field_labels:
            caption = text(key)
            if control in requested_controls:
                caption += " · " + text("tinysa.settings.requested_marker")
            label.setText(caption)
            control.setAccessibleName(caption)

    def set_locale(self) -> None:
        self.setAccessibleName(text("tinysa.settings.title"))
        for label, key in self._labels:
            label.setText(text(key))
        for combo, keys in self._options:
            with QSignalBlocker(combo):
                for index, key in enumerate(keys):
                    combo.setItemText(index, text(key))
        for field, _label, key in self._field_labels:
            field.setAccessibleName(text(key))
        self.rbw.setSuffix(text("tinysa.settings.unit.khz"))
        self.attenuation.setSuffix(text("tinysa_analyzer.unit.db"))
        self.screen_time.setSuffix(text("tinysa.common.seconds"))
        for numeric in (self.average, self.screen_time):
            numeric.setSpecialValueText(text("tinysa.settings.preserve"))
        self.readback.setText(text("tinysa.settings.readback"))
        self.readback.setAccessibleName(text("tinysa.settings.readback"))
        self.readback.setToolTip(text("tinysa.settings.readback.help"))
        self.scope.setText(text("tinysa.settings.scope"))
        self.correction.setItemText(0, text("tinysa.correction.none"))
        self.frontend_chain.setPlaceholderText(text("tinysa.correction.chain.placeholder"))
        self.extrapolate.setText(text("tinysa.correction.extrapolate"))
        self.extrapolate.setToolTip(text("tinysa.correction.extrapolate.help"))
        self.refresh_profiles.setText(text("tinysa.correction.refresh"))
        self._set_profile_scope()
        self._refresh_requested()

    def _set_profile_scope(self) -> None:
        message = text("tinysa.correction.scope" if self._profiles_model is not None
                       else "tinysa.correction.unavailable")
        if self._pending_profiles is not None and self._pending_profiles.error:
            # Fixed localized status; never leak a store path/raw exception.
            message = text("tinysa.correction.refresh_failed") + "\n" + message
        self.correction_scope.setText(message)

    def bind_profiles(self, model: CalibrationProfileViewModel | None) -> None:
        """Subscribe to the EXISTING profile store presenter; no I/O on Qt."""
        if self._unsubscribe_profiles is not None:
            self._unsubscribe_profiles()
            self._unsubscribe_profiles = None
        self._profiles_model = model
        if model is not None:
            self._unsubscribe_profiles = model.subscribe(self._on_profiles)
        self.set_locale()

    def release_profiles(self) -> None:
        self.bind_profiles(None)
        self._pending_profiles = None
        with QSignalBlocker(self.correction):
            self.correction.clear()
            self.correction.addItem(text("tinysa.correction.none"), None)

    def _refresh_profiles(self) -> None:
        if not self._profiles_locked and self._profiles_model is not None:
            self._profiles_model.refresh()  # existing off-Qt calibration executor

    def _on_profiles(self, state: CalibrationProfileViewState) -> None:
        self._pending_profiles = state
        self._set_profile_scope()
        self.refresh_profiles.setEnabled(not self._profiles_locked and not state.busy)
        if self._profiles_locked:
            return
        retained = self.correction.currentData()
        profiles = []
        for profile in state.profiles:
            if profile.signature.instrument_context is not None:
                profiles.append(profile)
                if len(profiles) == 128:
                    break
        # A refreshed store does not silently replace a captured immutable
        # curve with None/another version. Retain the explicit draft snapshot.
        if isinstance(retained, CalibrationProfile) and all(p.fingerprint != retained.fingerprint for p in profiles):
            profiles = profiles[:127] + [retained]
        with QSignalBlocker(self.correction):
            self.correction.clear()
            self.correction.addItem(text("tinysa.correction.none"), None)
            for profile in profiles:
                self.correction.addItem(text("tinysa.correction.item", name=profile.profile_id,
                    version=profile.profile_version, digest=profile.fingerprint[:8]), profile)
                if isinstance(retained, CalibrationProfile) and profile.fingerprint == retained.fingerprint:
                    self.correction.setCurrentIndex(self.correction.count() - 1)

    def correction_profile(self) -> CalibrationProfile | None:
        value = self.correction.currentData()
        return value if isinstance(value, CalibrationProfile) else None

    def apply_view_state(self, state: AnalyzerViewState) -> None:
        selection = state.source_selection
        source = selection.selected if selection is not None else None
        self.apply_source_state(source, revision=None if selection is None else selection.revision,
                                available=state.tinysa_controls_available,
                                controls_locked=state.controls_locked)

    def apply_source_state(self, source: AnalyzerSourceChoice | None, *,
                           revision: int | None, available: bool, controls_locked: bool,
                           allow_unobserved_draft: bool = False) -> None:
        """Reuse the draft for an independent pane, without selecting/opening it.

        Pane candidates can express intent before their explicit fresh Stage;
        this does NOT admit a command. Observed unknown firmware stays locked.
        Single-source callers retain their original observed-only policy.
        """
        snapshot = source.binding.snapshot if source is not None else None
        source_key = (source.device_id, revision) if source is not None else None
        # Observational revision/availability refresh is not a user edit.
        # Keep the request for the SAME source; fresh Stage still validates it.
        previous_source_id = None if self._source_key is None else self._source_key[0]
        source_id = None if source_key is None else source_key[0]
        if source_id != previous_source_id:
            for control, _keys in self._options:
                with QSignalBlocker(control):
                    control.setCurrentIndex(0)
            for numeric in (self.average, self.screen_time):
                with QSignalBlocker(numeric):
                    numeric.setValue(0)
            with QSignalBlocker(self.readback):
                self.readback.setChecked(False)
            with QSignalBlocker(self.rbw):
                self.rbw.setRange(2 if snapshot is not None and snapshot.model_id == "tinysa_basic" else .2,
                                  600 if snapshot is not None and snapshot.model_id == "tinysa_basic" else 850)
                self.rbw.setValue(30)
            with QSignalBlocker(self.attenuation):
                self.attenuation.setValue(10)
            with QSignalBlocker(self.correction):
                self.correction.setCurrentIndex(0)
            with QSignalBlocker(self.frontend_chain):
                self.frontend_chain.clear()
            with QSignalBlocker(self.extrapolate):
                self.extrapolate.setChecked(False)
        self._source_key = source_key
        known = snapshot is not None and snapshot.runtime_control_contract == TINYSA_RUNTIME_CONTROL_CONTRACT
        unobserved = (allow_unobserved_draft and source is not None
                      and source.family is DeviceFamily.TINYSA and snapshot is None)
        self.contract.setText(text("tinysa.settings.unobserved_draft" if unobserved else
                                   "tinysa.settings.known" if known else "tinysa.settings.unknown"))
        enabled = (known or unobserved) and available and not controls_locked
        was_locked = self._profiles_locked
        self._profiles_locked = controls_locked
        if was_locked and not self._profiles_locked and self._pending_profiles is not None:
            self._on_profiles(self._pending_profiles)
        ultra = snapshot is not None and snapshot.model_id == "tinysa_ultra"
        _set_option_enabled(self.input, 2, not ultra)
        _set_option_enabled(self.spur, 3, ultra)
        for control, _keys in self._options:
            control.setEnabled(enabled)
        self.lna.setEnabled(enabled and ultra)
        self.rbw.setEnabled(enabled and self.rbw_mode.currentData() == "manual")
        self.attenuation.setEnabled(enabled and self.attenuation_mode.currentData() == "manual")
        self.average.setEnabled(enabled)
        self.screen_time.setEnabled(enabled)
        self.readback.setEnabled(enabled)
        self.correction.setEnabled(enabled and self._profiles_model is not None)
        self.frontend_chain.setEnabled(enabled and self.correction_profile() is not None)
        self.extrapolate.setEnabled(enabled and self.correction_profile() is not None)
        self.refresh_profiles.setEnabled(available and not controls_locked
            and self._profiles_model is not None and not self._profiles_model.state.busy)
        self.lna.setToolTip(text("tinysa.settings.lna.help"))
        self.attenuation_mode.setToolTip(text("tinysa.settings.atten.help"))
        self._refresh_requested()
        if not available:
            self.hide()

    def plan(self) -> TinySaSweepSettingsPlan:
        rbw_mode = TinySaRbwMode(self.rbw_mode.currentData())
        atten_mode = TinySaAttenuationMode(self.attenuation_mode.currentData())
        return TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy(self.accuracy.currentData()),
            rbw_mode=rbw_mode, rbw_hz=round(self.rbw.value() * 1000) if rbw_mode is TinySaRbwMode.MANUAL else None,
            attenuation_mode=atten_mode,
            attenuation_db=self.attenuation.value() if atten_mode is TinySaAttenuationMode.MANUAL else None,
            lna=TinySaSwitchPolicy(self.lna.currentData()), spur_removal=TinySaSpurPolicy(self.spur.currentData()),
            repeat_count=self.average.value() or None,
            sweep_time_ms=round(self.screen_time.value() * 1000) if self.screen_time.value() else None)


def _set_option_enabled(control: QComboBox, index: int, enabled: bool) -> None:
    model = control.model()
    if isinstance(model, QStandardItemModel):
        item = model.item(index)
        if item is not None:
            item.setEnabled(enabled)
