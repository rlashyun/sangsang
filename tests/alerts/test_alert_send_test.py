from __future__ import annotations

import unittest
from unittest.mock import patch

from alert_fakes import TEST_SETTINGS, FakeEmailSender
from retriever_lost_found.alerts import send_test


class SendTestCommandTests(unittest.TestCase):
    def test_sends_one_mail_to_the_given_address(self) -> None:
        fake = FakeEmailSender()
        with (
            patch.object(send_test, "load_alert_settings", return_value=TEST_SETTINGS),
            patch.object(send_test, "create_email_sender", return_value=fake) as factory,
        ):
            self.assertEqual(send_test.main(["--to", "Me@Example.com"]), 0)

        self.assertEqual([email.to for email in fake.sent], ["me@example.com"])
        # MAIL_PROVIDER에 맞는 발송기(Gmail SMTP 또는 Resend)로 보낸다.
        factory.assert_called_once_with(TEST_SETTINGS.mail)

    def test_missing_settings_sends_nothing(self) -> None:
        with (
            patch.object(send_test, "load_alert_settings", return_value=None),
            patch.object(send_test, "create_email_sender") as sender,
        ):
            self.assertEqual(send_test.main(["--to", "me@example.com"]), 1)

        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
