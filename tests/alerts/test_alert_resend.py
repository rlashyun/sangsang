from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from alert_fakes import TEST_SETTINGS
from retriever_lost_found.alerts.resend_sender import ResendEmailSender
from retriever_lost_found.alerts.sender import (
    DailyLimitExceeded,
    LoginFailed,
    OutgoingEmail,
    PermanentSendError,
    SmtpEmailSender,
    TemporarySendError,
    create_email_sender,
    with_idempotency_key,
)
from retriever_lost_found.alerts.settings import MailSettings

RESEND_MAIL = MailSettings(
    provider="resend",
    api_key="re_secret_key_123",
    from_address="리트리버 <alert@retriever.example>",
)
EMAIL = OutgoingEmail(
    to="user@example.com",
    subject="[리트리버] 테스트",
    text="본문",
    html="<p>본문</p>",
    idempotency_key="alert-7-abc",
)
URLOPEN = "retriever_lost_found.alerts.resend_sender.urllib.request.urlopen"


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return b'{"id": "email-id"}'


def http_error(status: int, name: str) -> urllib.error.HTTPError:
    body = json.dumps({"statusCode": status, "name": name, "message": "re_secret_key_123 is bad"})
    return urllib.error.HTTPError(
        "https://api.resend.com/emails", status, name, {}, io.BytesIO(body.encode("utf-8"))
    )


class ResendSenderTests(unittest.TestCase):
    def send(self, email: OutgoingEmail = EMAIL):
        session = ResendEmailSender(RESEND_MAIL).open()
        try:
            session.send(email)
        finally:
            session.close()

    @patch(URLOPEN)
    def test_sends_one_api_call_with_the_key_and_the_idempotency_key(self, urlopen) -> None:
        urlopen.return_value = FakeResponse()

        self.send()

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer re_secret_key_123")
        # Resend는 같은 키로 24시간 안에 다시 보내면 한 번만 보낸다 — 중복 발송을 막는다.
        self.assertEqual(request.get_header("Idempotency-key"), "alert-7-abc")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["from"], "리트리버 <alert@retriever.example>")
        self.assertEqual(body["to"], ["user@example.com"])
        self.assertEqual((body["subject"], body["text"], body["html"]), ("[리트리버] 테스트", "본문", "<p>본문</p>"))

    @patch(URLOPEN)
    def test_no_idempotency_header_without_a_key(self, urlopen) -> None:
        urlopen.return_value = FakeResponse()

        self.send(OutgoingEmail(to="user@example.com", subject="s", text="t", html="h"))

        self.assertIsNone(urlopen.call_args.args[0].get_header("Idempotency-key"))

    @patch(URLOPEN)
    def test_errors_map_to_the_existing_send_outcomes(self, urlopen) -> None:
        cases = (
            (429, "daily_quota_exceeded", DailyLimitExceeded),  # 무료 플랜 하루 100통
            (429, "monthly_quota_exceeded", DailyLimitExceeded),  # 월 3,000통
            (429, "rate_limit_exceeded", TemporarySendError),  # 초당 요청 한도
            (403, "validation_error", LoginFailed),  # 도메인 미인증 등 설정 문제 — 실행을 멈춘다
            (401, "missing_api_key", LoginFailed),
            (422, "validation_error", PermanentSendError),  # 이 메일만 실패
            (500, "internal_server_error", TemporarySendError),
        )
        for status, name, expected in cases:
            with self.subTest(status=status, name=name):
                urlopen.side_effect = http_error(status, name)
                with self.assertRaises(expected) as caught:
                    self.send()
                self.assertNotIn("re_secret_key_123", str(caught.exception))

    @patch(URLOPEN)
    def test_network_failure_is_temporary(self, urlopen) -> None:
        urlopen.side_effect = urllib.error.URLError("timed out")

        with self.assertRaises(TemporarySendError):
            self.send()


class IdempotencyKeyTests(unittest.TestCase):
    def test_same_message_and_content_give_the_same_key(self) -> None:
        email = OutgoingEmail(to="user@example.com", subject="s", text="t", html="h")

        first = with_idempotency_key(email, 7)
        again = with_idempotency_key(email, 7)

        self.assertEqual(first.idempotency_key, again.idempotency_key)
        self.assertTrue(first.idempotency_key.startswith("alert-7-"))
        self.assertLessEqual(len(first.idempotency_key), 256)  # Resend 키 길이 상한

    def test_changed_content_or_another_message_gets_a_new_key(self) -> None:
        # 대기 중인 알림 메일에 물품이 더 붙으면 본문이 바뀐다 — 같은 키를 쓰면 Resend가 409로 거절한다.
        email = OutgoingEmail(to="user@example.com", subject="s", text="t", html="h")
        changed = OutgoingEmail(to="user@example.com", subject="s", text="t + 새 물품", html="h")

        self.assertNotEqual(with_idempotency_key(email, 7).idempotency_key, with_idempotency_key(changed, 7).idempotency_key)
        self.assertNotEqual(with_idempotency_key(email, 7).idempotency_key, with_idempotency_key(email, 8).idempotency_key)


class SenderFactoryTests(unittest.TestCase):
    def test_picks_the_sender_for_the_provider(self) -> None:
        self.assertIsInstance(create_email_sender(RESEND_MAIL), ResendEmailSender)
        self.assertIsInstance(create_email_sender(TEST_SETTINGS.mail), SmtpEmailSender)


if __name__ == "__main__":
    unittest.main()
