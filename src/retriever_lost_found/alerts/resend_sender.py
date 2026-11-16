"""설계 14장 — Resend HTTP API 발송기(2026-11-07 결정: Resend 무료 플랜 + 인증한 도메인).

오류는 SMTP 발송기와 같은 분류로 바꿔 발송 단계(dispatch)가 발신 수단을 몰라도 되게 한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .sender import (
    DailyLimitExceeded,
    LoginFailed,
    OutgoingEmail,
    PermanentSendError,
    SendError,
    TemporarySendError,
)
from .settings import MailSettings

RESEND_EMAILS_URL = "https://api.resend.com/emails"
# 무료 플랜 하루 100통·월 3,000통 — 넘기면 그날 발송을 멈추고 남은 메일은 다음 실행에서 보낸다.
_QUOTA_ERRORS = {"daily_quota_exceeded", "monthly_quota_exceeded"}


def classify_resend_error(status: int, name: str) -> SendError:
    if name in _QUOTA_ERRORS:
        return DailyLimitExceeded(f"Resend {status} {name}")
    if status in (401, 403):
        # API 키가 틀렸거나 도메인을 인증하지 않음 — 설정 문제라 이번 실행 전체를 멈춘다.
        return LoginFailed(f"Resend {status} {name}")
    if status == 429 or status >= 500:
        return TemporarySendError(f"Resend {status} {name}")
    return PermanentSendError(f"Resend {status} {name}")


def _error_name(error: urllib.error.HTTPError) -> str:
    # 응답 본문의 message는 남기지 않는다 — 비밀값이 섞일 수 있다(설계 8장).
    try:
        body = json.loads(error.read().decode("utf-8", errors="replace"))
    except (ValueError, OSError):
        return ""
    name = body.get("name") if isinstance(body, dict) else ""
    return str(name or "")[:64]


class ResendSession:
    def __init__(self, settings: MailSettings, timeout: float) -> None:
        self.settings = settings
        self.timeout = timeout

    def send(self, email: OutgoingEmail) -> None:
        payload = {
            "from": self.settings.from_address,
            "to": [email.to],
            "subject": email.subject,
            "html": email.html,
            "text": email.text,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "RetrieverLostFound/0.1",
        }
        if email.idempotency_key:
            headers["Idempotency-Key"] = email.idempotency_key
        request = urllib.request.Request(
            RESEND_EMAILS_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
        except urllib.error.HTTPError as error:
            raise classify_resend_error(error.code, _error_name(error)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TemporarySendError(f"Resend 연결 실패: {type(error).__name__}") from None

    def close(self) -> None:
        return None


class ResendEmailSender:
    """HTTP API라 연결을 미리 열지 않는다. API 키는 첫 발송 때 확인된다."""

    def __init__(self, settings: MailSettings, *, timeout: float = 20.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def open(self) -> ResendSession:
        return ResendSession(self.settings, self.timeout)
