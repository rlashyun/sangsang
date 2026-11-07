from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import DEFAULT_ENV_FILE, read_optional_env_value

# 설계 2장 — 발신 수단별 하루 발송 상한 기본값.
# smtp: Gmail 개인 계정 하루 약 500통보다 낮게(2026-11-06). resend: 무료 플랜 하루 100통(2026-11-07).
DEFAULT_DAILY_SEND_LIMITS = {"smtp": 450, "resend": 100}
COMMON_NAMES = ("MAIL_FROM", "PUBLIC_BASE_URL", "ALERT_SECRET")
PROVIDER_NAMES = {
    "smtp": ("MAIL_SMTP_HOST", "MAIL_SMTP_PORT", "MAIL_SMTP_USERNAME", "MAIL_SMTP_PASSWORD"),
    "resend": ("RESEND_API_KEY",),
}
# .env.example·CI·README에 모두 있어야 하는 이름(ALERT_DAILY_SEND_LIMIT는 선택이라 뺀다).
ALL_NAMES = ("MAIL_PROVIDER", *COMMON_NAMES, *PROVIDER_NAMES["smtp"], *PROVIDER_NAMES["resend"])


@dataclass(frozen=True)
class MailSettings:
    from_address: str
    provider: str = "smtp"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    api_key: str = ""

    def secrets(self) -> tuple[str, ...]:
        """오류 문구에서 가릴 값(설계 8장)."""
        return tuple(value for value in (self.password, self.api_key) if value)


@dataclass(frozen=True)
class AlertSettings:
    mail: MailSettings
    public_base_url: str
    secret: str
    daily_send_limit: int = DEFAULT_DAILY_SEND_LIMITS["smtp"]


def required_names(provider: str) -> tuple[str, ...]:
    return (*COMMON_NAMES, *PROVIDER_NAMES[provider])


def load_alert_settings(env_path: Path = DEFAULT_ENV_FILE) -> AlertSettings | None:
    """하나라도 없으면 None — 예약 API가 503을 돌려준다(Preview에서 실제 메일 차단, 설계 8장)."""
    provider = (read_optional_env_value("MAIL_PROVIDER", env_path) or "smtp").lower()
    if provider not in PROVIDER_NAMES:
        raise ValueError("MAIL_PROVIDER는 smtp 또는 resend여야 합니다.")
    values = {name: read_optional_env_value(name, env_path) for name in required_names(provider)}
    if not all(values.values()):
        return None

    if provider == "smtp":
        try:
            port = int(values["MAIL_SMTP_PORT"])
        except ValueError:
            raise ValueError("MAIL_SMTP_PORT는 숫자여야 합니다.") from None
        if port not in (465, 587):
            raise ValueError("MAIL_SMTP_PORT는 465 또는 587이어야 합니다.")
        mail = MailSettings(
            provider="smtp",
            host=values["MAIL_SMTP_HOST"],
            port=port,
            username=values["MAIL_SMTP_USERNAME"],
            password=values["MAIL_SMTP_PASSWORD"],
            from_address=values["MAIL_FROM"],
        )
    else:
        # 설계 14장 — MAIL_FROM은 Resend에서 인증한 도메인의 주소여야 한다.
        mail = MailSettings(
            provider="resend",
            api_key=values["RESEND_API_KEY"],
            from_address=values["MAIL_FROM"],
        )

    raw_limit = read_optional_env_value("ALERT_DAILY_SEND_LIMIT", env_path)
    try:
        daily_send_limit = int(raw_limit) if raw_limit else DEFAULT_DAILY_SEND_LIMITS[provider]
    except ValueError:
        raise ValueError("ALERT_DAILY_SEND_LIMIT는 숫자여야 합니다.") from None
    if not 1 <= daily_send_limit <= 500:
        raise ValueError("ALERT_DAILY_SEND_LIMIT는 1~500 사이여야 합니다.")
    return AlertSettings(
        mail=mail,
        public_base_url=values["PUBLIC_BASE_URL"].rstrip("/"),
        secret=values["ALERT_SECRET"],
        daily_send_limit=daily_send_limit,
    )
