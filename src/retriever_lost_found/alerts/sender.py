from __future__ import annotations

import hashlib
import smtplib
import ssl
from dataclasses import dataclass, replace
from email.message import EmailMessage
from typing import Protocol

from .settings import MailSettings


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    # 설계 14장 — 같은 메일을 다시 보낼 때 발송 서비스가 한 번만 보내게 하는 키(Resend Idempotency-Key)
    idempotency_key: str | None = None


def with_idempotency_key(email: OutgoingEmail, message_id: int) -> OutgoingEmail:
    """설계 14장 — 대기열 행 id와 내용으로 키를 만든다.

    같은 메일을 다시 보내면(함수가 보낸 직후 죽은 경우 등) 같은 키가 되어 Resend가 24시간 동안 한 번만 보낸다.
    대기 중인 알림 메일에 물품이 더 붙어 내용이 바뀌면 키도 바뀐다(같은 키·다른 본문은 409로 거절된다).
    """
    digest = hashlib.sha256(f"{email.to}\n{email.subject}\n{email.text}".encode("utf-8")).hexdigest()[:24]
    return replace(email, idempotency_key=f"alert-{int(message_id)}-{digest}")


class SendError(Exception):
    """메일 한 통을 보내지 못함."""


class DailyLimitExceeded(SendError):
    """Gmail 550 5.4.5 — 오늘은 더 보내지 않는다(설계 6.3)."""


class PermanentSendError(SendError):
    """5xx — 이 메일만 실패로 두고 다시 보내지 않는다."""


class TemporarySendError(SendError):
    """4xx·연결 오류 — 다음 실행에서 다시 보낸다."""


class LoginFailed(SendError):
    """SMTP 로그인 실패(앱 비밀번호 무효 등) — 이번 실행 전체를 멈춘다."""


class EmailSession(Protocol):
    def send(self, email: OutgoingEmail) -> None: ...

    def close(self) -> None: ...


class EmailSender(Protocol):
    def open(self) -> EmailSession: ...


def classify_smtp_error(code: int, response: bytes | str) -> SendError:
    text = response.decode("utf-8", errors="replace") if isinstance(response, bytes) else str(response)
    if code == 550 and "5.4.5" in text:
        return DailyLimitExceeded(f"SMTP {code} 하루 발송 한도 초과")
    if 500 <= code < 600:
        return PermanentSendError(f"SMTP {code}")
    return TemporarySendError(f"SMTP {code}")


class SmtpSession:
    def __init__(self, client: smtplib.SMTP, from_address: str) -> None:
        self.client = client
        self.from_address = from_address

    def send(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["Subject"] = email.subject
        message["From"] = self.from_address
        message["To"] = email.to
        message.set_content(email.text)
        message.add_alternative(email.html, subtype="html")
        try:
            refused = self.client.send_message(message)
        except smtplib.SMTPRecipientsRefused as error:
            code, response = next(iter(error.recipients.values()))
            raise classify_smtp_error(code, response) from None
        except smtplib.SMTPResponseException as error:
            raise classify_smtp_error(error.smtp_code, error.smtp_error) from None
        except (smtplib.SMTPException, OSError) as error:
            raise TemporarySendError(f"SMTP 전송 실패: {type(error).__name__}") from None
        if refused:
            code, response = next(iter(refused.values()))
            raise classify_smtp_error(code, response)

    def close(self) -> None:
        try:
            self.client.quit()
        except (smtplib.SMTPException, OSError):
            pass


class SmtpEmailSender:
    """설계 3.1 — Gmail 앱 비밀번호 SMTP. 465는 SSL, 587은 STARTTLS."""

    def __init__(self, settings: MailSettings, *, timeout: float = 20.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def open(self) -> SmtpSession:
        settings = self.settings
        context = ssl.create_default_context()
        try:
            if settings.port == 465:
                client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=self.timeout, context=context)
            else:
                client = smtplib.SMTP(settings.host, settings.port, timeout=self.timeout)
                client.starttls(context=context)
            client.login(settings.username, settings.password)
        except smtplib.SMTPAuthenticationError as error:
            # 앱 비밀번호가 오류 문구에 섞이지 않게 코드만 남긴다(설계 8장).
            raise LoginFailed(f"SMTP 로그인 실패 {error.smtp_code}") from None
        except smtplib.SMTPResponseException as error:
            raise classify_smtp_error(error.smtp_code, error.smtp_error) from None
        except (smtplib.SMTPException, OSError) as error:
            raise TemporarySendError(f"SMTP 연결 실패: {type(error).__name__}") from None
        return SmtpSession(client, settings.from_address)


def create_email_sender(settings: MailSettings, *, timeout: float = 20.0) -> EmailSender:
    """설계 14장 — MAIL_PROVIDER에 맞는 발송기. 바꿀 때 코드는 고치지 않는다."""
    if settings.provider == "resend":
        from .resend_sender import ResendEmailSender

        return ResendEmailSender(settings, timeout=timeout)
    return SmtpEmailSender(settings, timeout=timeout)
