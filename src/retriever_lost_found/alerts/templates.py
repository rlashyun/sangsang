from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from html import escape
from typing import Any

from ..privacy.masking import mask_item_row
from .links import MAX_EXTENSIONS, MailLinks, first_alert_day, last_alert_day
from .sender import OutgoingEmail

MAX_ITEMS_PER_MAIL = 5  # 설계 2장 — 메일 한 통에 물품 최대 5개
# 설계 7장 — 제목에는 사용자 입력을 넣지 않는다(헤더 주입 방지).
CONFIRM_SUBJECT = "[리트리버] 분실물 알림 예약을 확인해 주세요"
MATCH_SUBJECT = "[리트리버] 저장한 조건과 유사한 물품이 새로 등록됐습니다"
EXPIRY_SUBJECT = "[리트리버] 오늘로 분실물 알림이 끝납니다"
# AGENTS.md 2장 10번 부연 — "유사한 물품"으로 쓰고 본인 물건이라고 단정하지 않는다.
MATCH_HEADLINE = "저장한 조건과 유사한 물품이 새로 등록됐습니다."
OWNERSHIP_NOTICE = "본인 물건인지는 보관기관에 직접 확인해 주세요."
# PRD 4.3-E — 마지막 날 종료·연장 안내 문구(2026-11-08 결정)
FINAL_NOTICE = "이번 알림은 종료되며 이후에는 메일이 발송되지 않아요."
EXTEND_INVITATION = "계속 받고 싶다면 연장해 주세요."
VIEW_LABEL = "리트리버에서 보기"
_WEEKDAYS = "월화수목금토일"


def format_day(value: date) -> str:
    return f"{value.month}월 {value.day}일({_WEEKDAYS[value.weekday()]})"


def alert_period(reservation: dict[str, Any]) -> str:
    """PRD 4.3-E — D+1부터 마지막 날까지(연장하면 마지막 날만 뒤로 간다)."""
    first = format_day(first_alert_day(reservation["created_at"]))
    last = format_day(last_alert_day(reservation["expires_at"]))
    return f"{first}부터 {last}까지"


def _extensions_left(reservation: dict[str, Any]) -> int:
    return MAX_EXTENSIONS - int(reservation["extension_count"])


def radius_label(radius_m: int) -> str:
    """PRD 4.3-E — 1000·1500·2000m를 화면과 같은 1km·1.5km·2km로 쓴다."""
    return f"{int(radius_m) / 1000:g}km"


def _place_names(reservation: dict[str, Any]) -> str:
    """설계 16장 — 장소를 화면 순서대로 "서울역 · 시청"처럼 적는다. 반경은 하나를 함께 쓴다."""
    return " · ".join(place["name"] for place in reservation["places"])


def _condition_lines(reservation: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"장소: {_place_names(reservation)} 반경 {radius_label(reservation['radius_m'])} 안 모든 보관기관",
            f"검색어: {reservation['query']}",
            f"알림 기간: {alert_period(reservation)}",
        ]
    )


def _registered_label(value: Any) -> str | None:
    """등록일 "2026-11-15"를 "11월 15일 등록"으로 쓴다. 형식이 다르면 원래 값을 그대로 쓴다."""
    if not value:
        return None
    try:
        registered = date.fromisoformat(str(value)[:10])
    except ValueError:
        return f"{value} 등록"
    return f"{registered.month}월 {registered.day}일 등록"


def _item_line(item: dict[str, Any], *, show_place: bool) -> str:
    # PRD 3.3 · Q5 — 보낸 메일은 회수할 수 없으므로 물품명·설명은 가린 사본으로 쓴다.
    item = mask_item_row(item)
    details = ", ".join(value for value in (item.get("raw_category_name"), item.get("color_name")) if value)
    location = " ".join(
        value for value in (item.get("storage_location_name"), item.get("storage_location_phone")) if value
    )
    name = item.get("item_name") or "이름 없는 물품"
    parts = [name + (f" ({details})" if details else "")]
    # 설계 16장 — 장소가 여러 곳이면 보관기관에서 가장 가까운 장소를 붙인다(1곳이면 조건 줄과 같은 말이다).
    if show_place and item.get("near_place_name"):
        parts.append(f"{item['near_place_name']} 근처")
    registered = _registered_label(item.get("registered_on"))
    if registered:
        parts.append(registered)
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


def _item_lines(reservation: dict[str, Any], items: Sequence[dict[str, Any]], links: MailLinks) -> str:
    shown = items[:MAX_ITEMS_PER_MAIL]
    show_place = len(reservation["places"]) > 1
    lines = [f"{index}. {_item_line(item, show_place=show_place)}" for index, item in enumerate(shown, start=1)]
    hidden = len(items) - len(shown)
    if hidden > 0:
        # PRD 4.3-E(2026-11-24) — 결과 화면 링크가 있으면 메일이 알린 물품을 모두 그 화면에서 본다.
        rest = (
            f"'{VIEW_LABEL}'에서 모두 확인할 수 있어요." if links.results
            else "리트리버에서 같은 조건으로 검색해 확인해 주세요."
        )
        lines.append(f"그 밖에 {hidden}개가 더 있습니다. {rest}")
    return "\n".join(lines)


def _view_link(links: MailLinks) -> str:
    return links.results or links.home


def match_email(
    to: str,
    reservation: dict[str, Any],
    links: MailLinks,
    items: list[dict[str, Any]],
) -> OutgoingEmail:
    paragraphs = [
        f"{MATCH_HEADLINE} {OWNERSHIP_NOTICE}", _condition_lines(reservation), _item_lines(reservation, items, links)
    ]
    actions = [(VIEW_LABEL, _view_link(links))]
    if links.extend:
        actions.append((f"알림 1주 연장 (남은 연장 {_extensions_left(reservation)}회)", links.extend))
    actions.append(("예약 취소", links.cancel))
    return _render(to, MATCH_SUBJECT, paragraphs, actions)


def expiry_notice_email(
    to: str,
    reservation: dict[str, Any],
    links: MailLinks,
    items: Sequence[dict[str, Any]] = (),
) -> OutgoingEmail:
    """PRD 4.3-E — 마지막 날(D+7) 종료·연장 안내. 결과가 없어도 보내고, 그날 결과가 있으면 한 통에 합친다

    (발송 단위는 2026-11-15 담당 결정). 연장은 그날 자정(만료 시각) 전까지 된다.
    """
    if links.extend:
        closing = (
            f"{FINAL_NOTICE} {EXTEND_INVITATION} 연장은 오늘 밤 12시 전까지 할 수 있어요. "
            f"남은 연장은 {_extensions_left(reservation)}회예요."
        )
    else:
        closing = (
            f"{FINAL_NOTICE} 연장할 수 있는 횟수를 모두 사용했어요. "
            "계속 받고 싶다면 리트리버에서 새로 예약해 주세요."
        )
    paragraphs = [closing]
    if items:
        paragraphs += [f"{MATCH_HEADLINE} {OWNERSHIP_NOTICE}", _item_lines(reservation, items, links)]
    paragraphs.append(_condition_lines(reservation))
    actions: list[tuple[str, str]] = []
    if links.extend:
        actions.append(("알림 1주 연장", links.extend))
    else:
        actions.append(("리트리버에서 새로 예약하기", links.home))
    if items:
        actions.append((VIEW_LABEL, _view_link(links)))
    actions.append(("예약 취소", links.cancel))
    return _render(to, EXPIRY_SUBJECT, paragraphs, actions)
