"""③ 스케줄러 테스트용 설정·가짜 저장소·가짜 발송기. 외부 서비스를 부르지 않는다(AGENTS.md 7장)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from retriever_lost_found.alerts.settings import AlertSettings, MailSettings

# 2026-11-06 15:00 KST
NOW = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)

TEST_SETTINGS = AlertSettings(
    mail=MailSettings(
        host="smtp.example.com",
        port=465,
        username="sender@example.com",
        password="app-password-1234",
        from_address="리트리버 <sender@example.com>",
    ),
    public_base_url="https://retriever.example",
    secret="alert-test-secret",
    daily_send_limit=450,
)


def reservation_row(**overrides: Any) -> dict[str, Any]:
    """DB(REST·RPC)가 돌려주는 모양의 예약 행. 시각은 ISO 문자열이다."""
    row: dict[str, Any] = {
        "id": 7,
        "email": "user@example.com",
        "query": "에어팟",
        "place_name": "서울역",
        "radius_m": 3000,
        "status": "active",
        "link_nonce": "nonce-7",
        "created_at": NOW.isoformat(),
        "expires_at": "2026-11-13T02:00:00+00:00",
        "extension_count": 0,
    }
    row.update(overrides)
    return row
