from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from ..ingestion.collector import KOREA_TIMEZONE
from .settings import AlertSettings

# PRD 4.3-E — 등록일을 D일로 할 때 D+1부터 D+7까지 한국시간 달력상 7일 알린다.
RESERVATION_DAYS = 7
EXTENSION_DAYS = 7  # 설계 2장 — 연장 한 번에 +7일
MAX_EXTENSIONS = 3  # 설계 2장 — 최대 3회(최장 4주)
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
    """PRD 4.3-E — 마지막 날 D+7이 끝나는 순간, 곧 D+8 00:00 KST(2026-11-15 결정).

    D+7 발송 실행에서 종료·연장 안내를 보내고, 그날 자정 전까지 연장할 수 있다.
    이메일은 D+8 발송 실행의 정리 단계에서 지운다(화면 문구 "종료 다음 날 삭제").
    """
    local_date = created_at.astimezone(KOREA_TIMEZONE).date()
    return datetime.combine(
        local_date + timedelta(days=RESERVATION_DAYS + 1),
        time(0),
        tzinfo=KOREA_TIMEZONE,
    )


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def first_alert_day(created_at: str | datetime) -> date:
    """PRD 4.3-E — 알림은 등록 다음날(D+1)부터. 확인이 늦어도 일정은 등록일 기준이다."""
    return parse_timestamp(created_at).astimezone(KOREA_TIMEZONE).date() + timedelta(days=1)


def last_alert_day(expires_at: str | datetime) -> date:
    """만료 시각(자정) 바로 전 날짜 — 처음 예약이면 D+7, 연장하면 7일씩 뒤로 간다."""
    return (parse_timestamp(expires_at).astimezone(KOREA_TIMEZONE) - timedelta(seconds=1)).date()


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
