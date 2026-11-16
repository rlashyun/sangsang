from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retriever_lost_found.alerts.errors import safe_error
from retriever_lost_found.alerts.settings import (
    DEFAULT_DAILY_SEND_LIMITS,
    load_alert_settings,
    required_names,
)

VALUES = {
    "MAIL_PROVIDER": "",
    "MAIL_SMTP_HOST": "smtp.gmail.com",
    "MAIL_SMTP_PORT": "465",
    "MAIL_SMTP_USERNAME": "sender@example.com",
    "MAIL_SMTP_PASSWORD": "app-password",
    "RESEND_API_KEY": "",
    "MAIL_FROM": "리트리버 <sender@example.com>",
    "PUBLIC_BASE_URL": "https://retriever.example/",
    "ALERT_SECRET": "alert-secret",
    "ALERT_DAILY_SEND_LIMIT": "",
}
RESEND_VALUES = {
    **{name: "" for name in VALUES},
    "MAIL_PROVIDER": "resend",
    "RESEND_API_KEY": "re_test_key",
    "MAIL_FROM": "리트리버 <alert@retriever.example>",
    "PUBLIC_BASE_URL": "https://retriever.example",
    "ALERT_SECRET": "alert-secret",
}
# 존재하지 않는 파일 — 개발자 PC의 실제 .env를 읽지 않게 한다.
MISSING_ENV_FILE = Path(tempfile.gettempdir()) / "retriever-alert-settings-missing.env"


class AlertSettingsTests(unittest.TestCase):
    def load(self, base: dict[str, str] = VALUES, **overrides: str):
        with patch.dict(os.environ, {**base, **overrides}):
            return load_alert_settings(MISSING_ENV_FILE)

    def test_smtp_is_the_default_with_the_450_limit(self) -> None:
        settings = self.load()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.mail.provider, "smtp")
        self.assertEqual(settings.mail.port, 465)
        self.assertEqual(settings.public_base_url, "https://retriever.example")
        self.assertEqual(settings.daily_send_limit, 450)

    def test_resend_needs_only_its_api_key_and_defaults_to_100_a_day(self) -> None:
        # 2026-11-07 결정 — Resend 무료 플랜(하루 100통)으로 보낸다. SMTP 값은 없어도 된다.
        settings = self.load(RESEND_VALUES)

        self.assertIsNotNone(settings)
        self.assertEqual(settings.mail.provider, "resend")
        self.assertEqual(settings.mail.api_key, "re_test_key")
        self.assertEqual(settings.mail.from_address, "리트리버 <alert@retriever.example>")
        self.assertEqual(settings.daily_send_limit, 100)
        self.assertEqual(DEFAULT_DAILY_SEND_LIMITS, {"smtp": 450, "resend": 100})

    def test_any_missing_value_turns_alerts_off(self) -> None:
        for base, provider in ((VALUES, "smtp"), (RESEND_VALUES, "resend")):
            for name in required_names(provider):
                with self.subTest(provider=provider, name=name):
                    self.assertIsNone(self.load(base, **{name: ""}))

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "smtp 또는 resend"):
            self.load(MAIL_PROVIDER="sendgrid")

    def test_resend_limit_can_be_lowered(self) -> None:
        self.assertEqual(self.load(RESEND_VALUES, ALERT_DAILY_SEND_LIMIT="80").daily_send_limit, 80)

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
