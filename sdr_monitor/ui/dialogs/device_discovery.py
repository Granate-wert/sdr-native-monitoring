"""Non-modal device selection with useful empty and error states."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QListWidget, QPushButton, QVBoxLayout

from ...domain import DeviceDescriptor
from ..components import EmptyState, ErrorState, TaskProgress
from ..presenters import LivePresenter


class DeviceDiscoveryDialog(QDialog):
    device_selected = Signal(str)

    def __init__(self, presenter: LivePresenter, parent=None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self.setWindowTitle("Устройства SDR")
        self.setModal(False)
        layout = QVBoxLayout(self)
        self._progress = TaskProgress()
        self._progress.set_progress(0, "Поиск USB/IP устройств")
        self._devices = QListWidget()
        self._empty = EmptyState("Устройства не найдены", "Проверьте USB/IP соединение или повторите поиск.")
        self._error = ErrorState("Поиск пока не выполнялся")
        layout.addWidget(self._progress)
        layout.addWidget(self._devices)
        layout.addWidget(self._empty)
        layout.addWidget(self._error)
        manual = QHBoxLayout()
        self._manual_uri = QLineEdit()
        self._manual_uri.setPlaceholderText("usb:1.12.5 или ip:192.168.2.1")
        self._manual_uri.setAccessibleName("Ручной URI устройства")
        manual_button = QPushButton("Указать URI")
        manual_button.clicked.connect(self._select_manual_uri)
        manual.addWidget(self._manual_uri)
        manual.addWidget(manual_button)
        layout.addLayout(manual)
        actions = QHBoxLayout()
        refresh = QPushButton("Повторить поиск")
        refresh.clicked.connect(presenter.discover_devices)
        actions.addWidget(refresh)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        presenter.devices_discovered.connect(self._show_devices)
        presenter.task_failed.connect(self._show_error)
        presenter.busy_changed.connect(self._set_busy)

    def discover(self) -> None:
        self._presenter.discover_devices()

    def _show_devices(self, devices: tuple[DeviceDescriptor, ...]) -> None:
        self._devices.clear()
        for device in devices:
            self._devices.addItem(f"{device.label} — {device.uri}")
            self._devices.item(self._devices.count() - 1).setData(32, device.device_id)
        self._empty.setVisible(not devices)
        self._devices.setVisible(bool(devices))
        self._error.setVisible(False)

    def _show_error(self, message: str) -> None:
        self._error.setVisible(True)
        self._error.setAccessibleDescription(message)

    def _set_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        if not busy:
            self._progress.set_progress(100, "Поиск завершён")

    def _accept_selection(self) -> None:
        current = self._devices.currentItem()
        if current is None:
            return
        self.device_selected.emit(current.data(32))
        self.accept()

    def _select_manual_uri(self) -> None:
        uri = self._manual_uri.text().strip()
        if not uri:
            self._show_error("Введите URI USB или IP устройства")
            return
        self.device_selected.emit(f"manual:{uri}")
        self.accept()
