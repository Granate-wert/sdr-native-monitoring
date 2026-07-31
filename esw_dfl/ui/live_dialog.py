"""Device discovery dialog for the Live Monitor workspace.

P16UI-04: a small modal that scans USB/IP contexts (libiio discovery) or
accepts a manual ``usb:``/``ip:``/``local:`` URI.  The dialog itself never
touches native code directly: scanning goes through
:func:`esw_dfl.ui.live_discovery.discover_devices` with an injectable
scanner, so tests can supply a fake without hardware.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .i18n import LocaleId, Translator
from .live_discovery import DeviceKind, DiscoveredDevice, discover_devices

ScanCallback = Callable[[], tuple[DiscoveredDevice, ...]]


class DeviceDiscoveryDialog(QDialog):
    """Modal dialog: scan USB/IP devices or enter a manual URI."""

    def __init__(
        self,
        *,
        locale: LocaleId = LocaleId.RU,
        scanner: ScanCallback | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = Translator(locale)
        self._scanner = scanner
        self._selected: DiscoveredDevice | None = None
        self.setWindowTitle(self._tr.text("live.dialog.title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        self.status_label = QLabel(self._tr.text("live.dialog.scan_prompt"), self)
        self.status_label.setObjectName("liveDialogStatusLabel")
        layout.addWidget(self.status_label)

        self.device_list = QListWidget(self)
        self.device_list.setObjectName("liveDialogDeviceList")
        self.device_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.device_list, 1)

        self.scan_button = QPushButton(self._tr.text("live.dialog.scan"), self)
        self.scan_button.setObjectName("liveDialogScanButton")
        self.scan_button.clicked.connect(self._run_scan)
        layout.addWidget(self.scan_button)

        manual_row = QVBoxLayout()
        manual_row.addWidget(QLabel(self._tr.text("live.dialog.manual_uri"), self))
        self.uri_input = QLineEdit(self)
        self.uri_input.setObjectName("liveDialogUriInput")
        self.uri_input.setPlaceholderText("usb:1.2.3 / ip:192.168.2.1 / local:")
        manual_row.addWidget(self.uri_input)
        layout.addLayout(manual_row)

        self.add_button = QPushButton(self._tr.text("live.dialog.add"), self)
        self.add_button.setObjectName("liveDialogAddButton")
        self.add_button.clicked.connect(self._add_manual)
        layout.addWidget(self.add_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self._tr.text("live.dialog.ok"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._tr.text("live.dialog.cancel"))
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setAccessibleName("device_discovery_dialog")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def selected_device(self) -> DiscoveredDevice | None:
        return self._selected

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _run_scan(self) -> None:
        self.status_label.setText(self._tr.text("live.dialog.scanning"))
        try:
            devices = (
                self._scanner()
                if self._scanner is not None
                else discover_devices(filter="usb,ip")
            )
        except Exception as error:  # hardware failure must not crash the dialog
            self.status_label.setText(self._tr.text("live.dialog.scan_error", error=str(error)))
            return
        self.device_list.clear()
        for device in devices:
            item = QListWidgetItem(f"{_kind_label(device.kind, self._tr)} — {device.description}")
            item.setData(256, device.uri)
            item.setData(257, device.description)
            item.setData(258, device.kind.value)
            self.device_list.addItem(item)
        if not devices:
            self.status_label.setText(self._tr.text("live.dialog.empty"))
        else:
            self.status_label.setText(self._tr.text("live.dialog.found", count=len(devices)))

    def _add_manual(self) -> None:
        from .live_discovery import parse_manual_uri

        uri = parse_manual_uri(self.uri_input.text().strip())
        if uri is None:
            self.status_label.setText(self._tr.text("live.dialog.uri_invalid"))
            return
        device = DiscoveredDevice(uri=uri, description=uri, kind=DeviceKind.MANUAL)
        self._selected = device
        self.accept()

    def _on_selection_changed(self) -> None:
        item = self.device_list.currentItem()
        if item is None:
            self._selected = None
            return
        self._selected = DiscoveredDevice(
            uri=str(item.data(256)),
            description=str(item.data(257)),
            kind=DeviceKind(str(item.data(258))),
        )

    def _accept_selected(self) -> None:
        if self._selected is None:
            self.status_label.setText(self._tr.text("live.dialog.select_prompt"))
            return
        self.accept()


def _kind_label(kind: DeviceKind, tr: Translator) -> str:
    key = {
        DeviceKind.USB: "live.dialog.kind.usb",
        DeviceKind.IP: "live.dialog.kind.ip",
        DeviceKind.MANUAL: "live.dialog.kind.manual",
    }[kind]
    return tr.text(key)


def discovery_dialog_callback(
    *,
    locale: LocaleId = LocaleId.RU,
    scanner: ScanCallback | None = None,
    parent: QWidget | None = None,
) -> Callable[[], tuple[str, str, str] | None]:
    """Build the workspace ``discovery`` callable backed by the dialog.

    Returns ``(source_id, display_name, uri)`` on accept, ``None`` on
    cancel.  The workspace calls this on the GUI thread; the dialog is
    modal but the underlying scan is hardware-bound, so the caller should
    run it from a button handler only.
    """

    def _discover() -> tuple[str, str, str] | None:
        dialog = DeviceDiscoveryDialog(locale=locale, scanner=scanner, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        device = dialog.selected_device()
        if device is None:
            return None
        return (device.uri, device.description, device.uri)

    return _discover


__all__ = [
    "DeviceDiscoveryDialog",
    "discovery_dialog_callback",
]
