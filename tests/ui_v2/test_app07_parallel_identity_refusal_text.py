"""Pure localization checks for the AD936x parallel-identity Stage refusal."""

from __future__ import annotations

import unittest

from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.ui.v2.i18n import UiLocale, catalog_keys, text


class ParallelIdentityRefusalTextTests(unittest.TestCase):
    def test_reason_resolves_to_concise_english_and_russian_copy(self) -> None:
        reason = PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED
        key = f"analyzer.pane.setup.refusal.{reason.value}"
        self.assertIn(key, catalog_keys())

        english = text(key, UiLocale.EN)
        russian = text(key, UiLocale.RU)
        self.assertIn("AD936x", english)
        self.assertIn("not confirmed to be independent", english)
        self.assertIn("Stage was refused", english)
        self.assertIn("RF/RX did not start", english)
        self.assertIn("USB and IP routes", english)
        self.assertIn("same device", english)

        self.assertIn("AD936x", russian)
        self.assertIn("не подтверждено", russian.casefold())
        self.assertIn("независимы", russian.casefold())
        self.assertIn("подготовка отклонена", russian.casefold())
        self.assertIn("RF/RX не запускались", russian)
        self.assertIn("USB и IP", russian)
        self.assertIn("одному устройству", russian)

    def test_refusal_does_not_claim_a_device_fault_or_request_reconfiguration(self) -> None:
        key = f"analyzer.pane.setup.refusal.{PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED.value}"
        forbidden = (
            "busy", "disconnected", "throughput", "connect a usb cable", "change firmware",
            "занят", "отключен", "пропускная способность", "подключите кабель", "смените прошивку",
        )
        for locale in (UiLocale.EN, UiLocale.RU):
            value = text(key, locale).casefold()
            with self.subTest(locale=locale):
                for phrase in forbidden:
                    self.assertNotIn(phrase, value)


if __name__ == "__main__":
    unittest.main()
