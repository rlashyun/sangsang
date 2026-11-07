from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from .links import KOREA_TIMEZONE, MAX_EXTENSIONS, MailLinks, parse_timestamp
from .sender import OutgoingEmail

MAX_ITEMS_PER_MAIL = 5  # 설계 2장 — 메일 한 통에 물품 최대 5개
# 설계 7장 — 제목에는 사용자 입력을 넣지 않는다(헤더 주입 방지).
CONFIRM_SUBJECT = "[리트리버] 분실물 알림 예약을 확인해 주세요"
MATCH_SUBJECT = "[리트리버] 저장한 조건과 유사한 물품이 새로 등록됐습니다"
EXPIRY_SUBJECT = "[리트리버] 분실물 알림 예약이 내일 끝납니다"
# AGENTS.md 2장 10번 부연 — "유사한 물품"으로 쓰고 본인 물건이라고 단정하지 않는다.
MATCH_HEADLINE = "저장한 조건과 유사한 물품이 새로 등록됐습니다."
OWNERSHIP_NOTICE = "본인 물건인지는 보관기관에 직접 확인해 주세요."
_WEEKDAYS = "월화수목금토일"


def format_expiry(value: str | datetime) -> str:
    local = parse_timestamp(value).astimezone(KOREA_TIMEZONE)
    period = "오전" if local.hour < 12 else "오후"
    hour = local.hour if local.hour <= 12 else local.hour - 12
    return f"{local.month}월 {local.day}일({_WEEKDAYS[local.weekday()]}) {period} {hour}시"


def _extensions_left(reservation: dict[str, Any]) -> int:
    return MAX_EXTENSIONS - int(reservation["extension_count"])


def _condition_lines(reservation: dict[str, Any]) -> str:
    radius_km = int(reservation["radius_m"]) // 1000
    return "\n".join(
        [
            f"장소: {reservation['place_name']} 반경 {radius_km}km 안 모든 보관기관",
            f"검색어: {reservation['query']}",
            f"알림 기간: {format_expiry(reservation['expires_at'])}까지",
        ]
    )


def _item_line(item: dict[str, Any]) -> str:
    details = ", ".join(value for value in (item.get("raw_category_name"), item.get("color_name")) if value)
    location = " ".join(
        value for value in (item.get("storage_location_name"), item.get("storage_location_phone")) if value
    )
    name = item.get("item_name") or "이름 없는 물품"
    parts = [name + (f" ({details})" if details else ""), f"{item.get('registered_on')} 등록"]
    if location:
        parts.append(f"보관: {location}")
    return " · ".join(parts)


def _render(to: str, subject: str, paragraphs: list[str], actions: list[tuple[str, str]]) -> OutgoingEmail:
    """paragraphs에는 사용자 입력이 들어 있다 — HTML에서는 모두 escape한다(설계 7장)."""
    action_lines = "\n".join(f"{label}: {url}" for label, url in actions)
    html_parts = [f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>" for paragraph in paragraphs]
    html_parts += [f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>' for label, url in actions]
    return OutgoingEmail(
        to=to,
        subject=subject,
        text="\n\n".join([*paragraphs, action_lines]),
        html="<html><body>" + "".join(html_parts) + "</body></html>",
    )


def confirmation_email(to: str, reservation: dict[str, Any], links: MailLinks) -> OutgoingEmail:
    paragraphs = [
        "리트리버 분실물 알림 예약을 신청하셨습니다.",
        _condition_lines(reservation),
        "아래 '예약 확인'을 눌러야 알림이 켜집니다. 이 링크는 24시간 동안 유효합니다.",
        "직접 신청하지 않으셨다면 이 메일을 무시하세요. 확인하지 않은 예약은 자동으로 사라집니다.",
    ]
    actions = [("예약 확인", links.confirm), ("예약 취소", links.cancel)]
    return _render(to, CONFIRM_SUBJECT, paragraphs, [(label, url) for label, url in actions if url])


def match_email(
    to: str,
    reservation: dict[str, Any],
    links: MailLinks,
    items: list[dict[str, Any]],
) -> OutgoingEmail:
    shown = items[:MAX_ITEMS_PER_MAIL]
    lines = [f"{index}. {_item_line(item)}" for index, item in enumerate(shown, start=1)]
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append(f"그 밖에 {hidden}개가 더 있습니다. 리트리버에서 같은 조건으로 검색해 확인해 주세요.")
    paragraphs = [f"{MATCH_HEADLINE} {OWNERSHIP_NOTICE}", _condition_lines(reservation), "\n".join(lines)]
    actions = [("리트리버에서 보기", links.home)]
    if links.extend:
        actions.append((f"알림 1주 연장 (남은 연장 {_extensions_left(reservation)}회)", links.extend))
    actions.append(("예약 취소", links.cancel))
    return _render(to, MATCH_SUBJECT, paragraphs, actions)


def expiry_notice_email(to: str, reservation: dict[str, Any], links: MailLinks) -> OutgoingEmail:
    paragraphs = [
        f"분실물 알림 예약이 {format_expiry(reservation['expires_at'])}에 끝납니다.",
        _condition_lines(reservation),
    ]
    actions: list[tuple[str, str]] = []
    if links.extend:
        paragraphs.append(
            f"더 받으려면 '알림 1주 연장'을 눌러 주세요. 남은 연장은 {_extensions_left(reservation)}회입니다."
        )
        actions.append(("알림 1주 연장", links.extend))
    else:
        paragraphs.append("연장할 수 있는 횟수를 모두 사용했습니다. 계속 받으려면 리트리버에서 새로 예약해 주세요.")
        actions.append(("리트리버에서 새로 예약하기", links.home))
    actions.append(("예약 취소", links.cancel))
    return _render(to, EXPIRY_SUBJECT, paragraphs, actions)
