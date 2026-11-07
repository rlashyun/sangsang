from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from .settings import AlertSettings

KOREA_TIMEZONE = timezone(timedelta(hours=9))
RESERVATION_DAYS = 7  # PRD 4.2 P1 — 등록한 날부터 1주일
EXTENSION_DAYS = 7  # 설계 2장 — 연장 한 번에 +7일
MAX_EXTENSIONS = 3  # 설계 2장 — 최대 3회(최장 4주)
EXPIRY_HOUR_KST = 11  # 설계 5.3 — 발송 크론(KST 11시대)과 맞춘다
# 메일 속 링크가 여는 프런트 경로. 강지현과 합의가 남았다(설계 13장) — 바뀌면 여기만 고친다.
LINK_PATHS = {
    "confirm": "/alerts/confirm",
    "extend": "/alerts/extend",
    "cancel": "/alerts/cancel",
}
_TOKEN_PATTERN = re.compile(r"([1-9]\d{0,17})\.([0-9a-f]{32})")


@dataclass(frozen=True)
class MailLinks:
    home: str
    cancel: str
    confirm: str | None = None
    extend: str | None = None


def new_link_nonce() -> str:
    return secrets.token_hex(16)


def link_token(secret: str, reservation_id: int, nonce: str) -> str:
    """설계 5.1 — DB에 저장하지 않고 메일을 보낼 때마다 다시 만든다."""
    digest = hmac.new(
        secret.encode("utf-8"),
        f"link:{reservation_id}:{nonce}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{reservation_id}.{digest[:32]}"


def token_reservation_id(token: str) -> int:
    match = _TOKEN_PATTERN.fullmatch(token.strip())
    if match is None:
        raise ValueError("링크가 올바르지 않습니다.")
    return int(match.group(1))


def token_matches(secret: str, token: str, nonce: str) -> bool:
    reservation_id = token_reservation_id(token)
    return hmac.compare_digest(link_token(secret, reservation_id, nonce), token.strip())


def ip_hash(secret: str, ip: str) -> str | None:
    """설계 5.2 — IP 원문은 저장하지 않는다."""
    ip = ip.strip()
    if not ip:
        return None
    return hmac.new(secret.encode("utf-8"), f"ip:{ip}".encode("utf-8"), hashlib.sha256).hexdigest()


def expires_at_for(created_at: datetime) -> datetime:
    """설계 5.3 — 등록한 날(KST 날짜) + 7일의 11:00 KST."""
    local_date = created_at.astimezone(KOREA_TIMEZONE).date()
    return datetime.combine(
        local_date + timedelta(days=RESERVATION_DAYS),
        time(EXPIRY_HOUR_KST),
        tzinfo=KOREA_TIMEZONE,
    )


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def mail_links(settings: AlertSettings, reservation: dict[str, Any], now: datetime) -> MailLinks:
    token = link_token(settings.secret, int(reservation["id"]), str(reservation["link_nonce"]))
    base = settings.public_base_url

    def url(action: str) -> str:
        return f"{base}{LINK_PATHS[action]}?token={token}"

    can_extend = (
        reservation["status"] == "active"
        and int(reservation["extension_count"]) < MAX_EXTENSIONS
        and parse_timestamp(reservation["expires_at"]) > now
    )
    return MailLinks(
        home=f"{base}/",
        cancel=url("cancel"),
        confirm=url("confirm") if reservation["status"] == "pending" else None,
        extend=url("extend") if can_extend else None,
    )
