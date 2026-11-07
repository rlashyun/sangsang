from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retriever_lost_found.alerts.errors import safe_error
from retriever_lost_found.alerts.settings import (
    DEFAULT_DAILY_SEND_LIMIT,
    REQUIRED_NAMES,
    load_alert_settings,
)

VALUES = {
    "MAIL_SMTP_HOST": "smtp.gmail.com",
    "MAIL_SMTP_PORT": "465",
    "MAIL_SMTP_USERNAME": "sender@example.com",
    "MAIL_SMTP_PASSWORD": "app-password",
    "MAIL_FROM": "리트리버 <sender@example.com>",
    "PUBLIC_BASE_URL": "https://retriever.example/",
    "ALERT_SECRET": "alert-secret",
    "ALERT_DAILY_SEND_LIMIT": "",
}
# 존재하지 않는 파일 — 개발자 PC의 실제 .env를 읽지 않게 한다.
MISSING_ENV_FILE = Path(tempfile.gettempdir()) / "retriever-alert-settings-missing.env"


class AlertSettingsTests(unittest.TestCase):
    def load(self, **overrides: str):
        with patch.dict(os.environ, {**VALUES, **overrides}):
            return load_alert_settings(MISSING_ENV_FILE)

    def test_reads_mail_settings_with_the_450_default(self) -> None:
        settings = self.load()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.mail.port, 465)
        self.assertEqual(settings.public_base_url, "https://retriever.example")
        self.assertEqual(settings.daily_send_limit, DEFAULT_DAILY_SEND_LIMIT)
        self.assertEqual(DEFAULT_DAILY_SEND_LIMIT, 450)

    def test_any_missing_value_turns_alerts_off(self) -> None:
        for name in REQUIRED_NAMES:
            with self.subTest(name=name):
                self.assertIsNone(self.load(**{name: ""}))

    def test_only_ssl_or_starttls_ports(self) -> None:
        self.assertEqual(self.load(MAIL_SMTP_PORT="587").mail.port, 587)
        with self.assertRaisesRegex(ValueError, "465 또는 587"):
            self.load(MAIL_SMTP_PORT="25")

    def test_daily_limit_stays_under_the_gmail_limit(self) -> None:
        self.assertEqual(self.load(ALERT_DAILY_SEND_LIMIT="300").daily_send_limit, 300)
        with self.assertRaisesRegex(ValueError, "1~500"):
            self.load(ALERT_DAILY_SEND_LIMIT="600")


class SafeErrorTests(unittest.TestCase):
    def test_hides_secrets_and_keeps_the_rest(self) -> None:
        message = safe_error(RuntimeError("login failed for app-password"), ("app-password", ""))

        self.assertNotIn("app-password", message)
        self.assertIn("[REDACTED]", message)
        self.assertIn("RuntimeError: login failed for", message)


if __name__ == "__main__":
    unittest.main()
