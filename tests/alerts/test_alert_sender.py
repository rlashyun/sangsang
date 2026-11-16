from __future__ import annotations

import smtplib
import unittest
from dataclasses import replace
from unittest.mock import patch

from alert_fakes import TEST_SETTINGS
from retriever_lost_found.alerts.sender import (
    DailyLimitExceeded,
    LoginFailed,
    OutgoingEmail,
    PermanentSendError,
    SmtpEmailSender,
    TemporarySendError,
    classify_smtp_error,
)

EMAIL = OutgoingEmail(to="user@example.com", subject="[리트리버] 테스트", text="본문", html="<p>본문</p>")


class FakeSmtp:
    instances: list["FakeSmtp"] = []
    login_error: Exception | None = None
    send_error: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.messages = []
        self.quit_called = False
        FakeSmtp.instances.append(self)

    def starttls(self, context=None) -> None:
        self.started_tls = True

    def login(self, username, password) -> None:
        if FakeSmtp.login_error is not None:
            raise FakeSmtp.login_error
        self.logged_in = (username, password)

    def send_message(self, message):
        if FakeSmtp.send_error is not None:
            raise FakeSmtp.send_error
        self.messages.append(message)
        return {}

    def quit(self) -> None:
        self.quit_called = True


class SmtpSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSmtp.instances = []
        FakeSmtp.login_error = None
        FakeSmtp.send_error = None

    def open_session(self, mail=TEST_SETTINGS.mail):
        with (
            patch("retriever_lost_found.alerts.sender.smtplib.SMTP_SSL", FakeSmtp),
            patch("retriever_lost_found.alerts.sender.smtplib.SMTP", FakeSmtp),
        ):
            return SmtpEmailSender(mail).open()

    def test_port_465_logs_in_over_ssl_and_sends_text_and_html(self) -> None:
        session = self.open_session()
        session.send(EMAIL)
        session.close()

        client = FakeSmtp.instances[0]
        self.assertEqual((client.host, client.port), ("smtp.example.com", 465))
        self.assertFalse(client.started_tls)
        self.assertEqual(client.logged_in, ("sender@example.com", "app-password-1234"))
        message = client.messages[0]
        self.assertEqual(message["To"], "user@example.com")
        self.assertEqual(message["From"], TEST_SETTINGS.mail.from_address)
        self.assertEqual(message["Subject"], "[리트리버] 테스트")
        self.assertEqual(
            [part.get_content_type() for part in message.iter_parts()],
            ["text/plain", "text/html"],
        )
        self.assertTrue(client.quit_called)

    def test_port_587_upgrades_with_starttls(self) -> None:
        self.open_session(replace(TEST_SETTINGS.mail, port=587))

        self.assertTrue(FakeSmtp.instances[0].started_tls)

    def test_login_failure_stops_the_run_without_leaking_the_password(self) -> None:
        FakeSmtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

        with self.assertRaises(LoginFailed) as caught:
            self.open_session()

        self.assertNotIn("app-password-1234", str(caught.exception))

    def test_gmail_daily_limit_is_its_own_error(self) -> None:
        session = self.open_session()
        FakeSmtp.send_error = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded.")

        with self.assertRaises(DailyLimitExceeded):
            session.send(EMAIL)

    def test_other_5xx_fails_only_this_mail(self) -> None:
        session = self.open_session()
        FakeSmtp.send_error = smtplib.SMTPRecipientsRefused(
            {"user@example.com": (550, b"5.1.1 The email account does not exist")}
        )

        with self.assertRaises(PermanentSendError):
            session.send(EMAIL)

    def test_4xx_and_disconnects_are_temporary(self) -> None:
        session = self.open_session()
        FakeSmtp.send_error = smtplib.SMTPDataError(421, b"4.7.0 Try again later")
        with self.assertRaises(TemporarySendError):
            session.send(EMAIL)

        FakeSmtp.send_error = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        with self.assertRaises(TemporarySendError):
            session.send(EMAIL)

    def test_classifier_keeps_the_daily_limit_apart_from_other_550(self) -> None:
        self.assertIsInstance(classify_smtp_error(550, b"5.4.5 Daily user sending limit exceeded"), DailyLimitExceeded)
        self.assertIsInstance(classify_smtp_error(550, "5.1.1 no such user"), PermanentSendError)
        self.assertIsInstance(classify_smtp_error(451, b"4.3.0 try later"), TemporarySendError)


if __name__ == "__main__":
    unittest.main()
