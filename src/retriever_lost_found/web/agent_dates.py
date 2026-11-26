"""Resolve the date expression extracted by the search agent in Korean time."""

import re
from datetime import date, datetime, timedelta, timezone

from ..ingestion.collector import SOURCE_RETENTION_MONTHS, retention_start


_KST = timezone(timedelta(hours=9))
_LAST_WEEK = re.compile(r"(?:지난|저번)주(?:([월화수목금토일])(?:요일)?)?")
_CALENDAR_DATE = re.compile(r"(?:(\d{4})년)?(\d{1,2})월(\d{1,2})일")
_NUMERIC_DATE = re.compile(r"(?:(\d{4})[./-])?(\d{1,2})[./-](\d{1,2})")


def korea_today() -> date:
    return datetime.now(_KST).date()


class FutureDateError(ValueError):
    """PRD 4.3-A — 아직 오지 않은 분실일. 안내 문장에 날짜를 쓰도록 함께 전달한다."""

    def __init__(self, lost_on: date):
        super().__init__("분실일은 미래일 수 없습니다.")
        self.lost_on = lost_on


def resolve_lost_on(expression: str, today: date, previous: str | None = None) -> str:
    """Return the inclusive lower bound of the registration-date filter."""
    compact = re.sub(r"\s+", "", expression)
    future_days = {"내일": 1, "모레": 2, "글피": 3}
    if compact in future_days:
        raise FutureDateError(today + timedelta(days=future_days[compact]))
    if compact in {"오늘", "어제", "그제", "그저께", "엊그제"}:
        days_ago = {"오늘": 0, "어제": 1, "그제": 2, "그저께": 2, "엊그제": 2}[compact]
        return (today - timedelta(days=days_ago)).isoformat()
    if match := _LAST_WEEK.fullmatch(compact):
        last_monday = today - timedelta(days=today.weekday() + 7)
        weekday = "월화수목금토일".index(match[1]) if match[1] else 0
        return (last_monday + timedelta(days=weekday)).isoformat()

    if match := re.fullmatch(r"(\d{1,3})일전", compact):
        return (today - timedelta(days=int(match[1]))).isoformat()

    if match := re.fullmatch(r"(\d{1,2})일", compact):
        anchor = date.fromisoformat(previous) if previous else today
        lost_on = anchor.replace(day=int(match[1]))
        if lost_on <= today:
            return lost_on.isoformat()
        raise FutureDateError(lost_on)

    match = _CALENDAR_DATE.fullmatch(compact) or _NUMERIC_DATE.fullmatch(compact)
    if not match:
        raise ValueError("지원하지 않는 분실일 표현")
    year, month, day = match.groups()
    lost_on = date(int(year) if year else today.year, int(month), int(day))
    if not year and lost_on > today:
        # PRD 3.1-2 — 작년 날짜는 보존기간 안일 때만 뜻이 통한다. 그보다 오래되면 미래 날짜로 본다.
        previous = retention_start(lost_on, 12)
        if previous >= retention_start(today, max(SOURCE_RETENTION_MONTHS.values())):
            return previous.isoformat()
    if lost_on > today:
        raise FutureDateError(lost_on)
    return lost_on.isoformat()
