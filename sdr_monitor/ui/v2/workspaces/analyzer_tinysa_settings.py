"""Model/firmware-aware tinySA draft drawer; no device or session ownership."""

from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
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


class TinySaSettingsDrawer(QFrame):
    draft_changed = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("v2-tinysa-settings-drawer")
        self.setProperty("ui2Role", "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._revision: int | None = None
        self._profiles_model: CalibrationProfileViewModel | None = None
        self._unsubscribe_profiles: Callable[[], None] | None = None
        self._profiles_locked = False
        self._pending_profiles: CalibrationProfileViewState | None = None
        self._labels: list[tuple[QLabel | QPushButton, str]] = []
        self._field_labels: list[tuple[QWidget, str]] = []
        self._options: list[tuple[QComboBox, tuple[str, ...]]] = []
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
        self.correction = QComboBox(self)
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
        control = QComboBox(self)
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
        self._field_labels.append((control, key))
        control.setAccessibleName(text(key))

    def _numeric(self, control: QDoubleSpinBox | QSpinBox, key: str) -> None:
        control.setProperty("ui2Role", "range-control")
        control.setKeyboardTracking(False)
        self._add_field(control, key)
        control.valueChanged.connect(self._changed)

    def _changed(self, _value: object = None) -> None:
        self.draft_changed.emit()

    def set_locale(self) -> None:
        self.setAccessibleName(text("tinysa.settings.title"))
        for label, key in self._labels:
            label.setText(text(key))
        for combo, keys in self._options:
            for index, key in enumerate(keys):
                combo.setItemText(index, text(key))
        for field, key in self._field_labels:
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
        snapshot = source.binding.snapshot if source is not None else None
        revision = selection.revision if state.tinysa_controls_available and selection is not None else None
        if revision != self._revision:
            self._revision = revision
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
        known = snapshot is not None and snapshot.runtime_control_contract == TINYSA_RUNTIME_CONTROL_CONTRACT
        self.contract.setText(text("tinysa.settings.known" if known else "tinysa.settings.unknown"))
        enabled = known and state.tinysa_controls_available and not state.controls_locked
        was_locked = self._profiles_locked
        self._profiles_locked = state.controls_locked
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
        self.refresh_profiles.setEnabled(state.tinysa_controls_available and not state.controls_locked
            and self._profiles_model is not None and not self._profiles_model.state.busy)
        self.lna.setToolTip(text("tinysa.settings.lna.help"))
        self.attenuation_mode.setToolTip(text("tinysa.settings.atten.help"))
        if not state.tinysa_controls_available:
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
