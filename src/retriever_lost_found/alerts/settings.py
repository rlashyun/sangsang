from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import DEFAULT_ENV_FILE, read_optional_env_value

# 설계 2장 — Gmail 개인 계정 하루 약 500통보다 낮게 잡은 상한(2026-11-06 결정)
DEFAULT_DAILY_SEND_LIMIT = 450
REQUIRED_NAMES = (
    "MAIL_SMTP_HOST",
    "MAIL_SMTP_PORT",
    "MAIL_SMTP_USERNAME",
    "MAIL_SMTP_PASSWORD",
    "MAIL_FROM",
    "PUBLIC_BASE_URL",
    "ALERT_SECRET",
)


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str
    from_address: str


@dataclass(frozen=True)
class AlertSettings:
    mail: MailSettings
    public_base_url: str
    secret: str
    daily_send_limit: int = DEFAULT_DAILY_SEND_LIMIT


def load_alert_settings(env_path: Path = DEFAULT_ENV_FILE) -> AlertSettings | None:
    """하나라도 없으면 None — 예약 API가 503을 돌려준다(Preview에서 실제 메일 차단, 설계 8장)."""
    values = {name: read_optional_env_value(name, env_path) for name in REQUIRED_NAMES}
    if not all(values.values()):
        return None
    try:
        port = int(values["MAIL_SMTP_PORT"])
    except ValueError:
        raise ValueError("MAIL_SMTP_PORT는 숫자여야 합니다.") from None
    if port not in (465, 587):
        raise ValueError("MAIL_SMTP_PORT는 465 또는 587이어야 합니다.")
    raw_limit = read_optional_env_value("ALERT_DAILY_SEND_LIMIT", env_path)
    try:
        daily_send_limit = int(raw_limit) if raw_limit else DEFAULT_DAILY_SEND_LIMIT
    except ValueError:
        raise ValueError("ALERT_DAILY_SEND_LIMIT는 숫자여야 합니다.") from None
    if not 1 <= daily_send_limit <= 500:
        raise ValueError("ALERT_DAILY_SEND_LIMIT는 1~500 사이여야 합니다.")
    return AlertSettings(
        mail=MailSettings(
            host=values["MAIL_SMTP_HOST"],
            port=port,
            username=values["MAIL_SMTP_USERNAME"],
            password=values["MAIL_SMTP_PASSWORD"],
            from_address=values["MAIL_FROM"],
        ),
        public_base_url=values["PUBLIC_BASE_URL"].rstrip("/"),
        secret=values["ALERT_SECRET"],
        daily_send_limit=daily_send_limit,
    )
