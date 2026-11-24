from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..config import read_env_value, read_optional_env_value
from ..integrations.supabase import SupabaseFoundItemClient
from ..integrations.supabase_rest import SupabaseRestClient

# 설계 16장 — 장소는 alert_reservation_places에서 화면 순서(position)대로 함께 읽는다.
RESERVATION_FIELDS = (
    "id,email,query,radius_m,status,link_nonce,created_at,expires_at,extension_count,"
    "places:alert_reservation_places(name:place_name,position,latitude,longitude)"
)


@dataclass(frozen=True)
class AlertPlace:
    """알림 기준 장소 한 곳(설계 16장). 화면 Place의 name·latitude·longitude다."""

    name: str
    latitude: float
    longitude: float


def _ordered_places(places: Any) -> list[dict[str, Any]]:
    # 좌표는 결과 링크 화면의 지도에만 쓴다(링크 토큰을 가진 본인). 요약·메일은 이름만 꺼낸다.
    rows = sorted(places or [], key=lambda place: place.get("position") or 0)
    return [
        {"name": str(place["name"]), "latitude": float(place["latitude"]), "longitude": float(place["longitude"])}
        for place in rows
    ]


class AlertStore(Protocol):
    """설계 4.6 — 알림 예약 저장소. SupabaseAlertStore와 테스트의 FakeAlertStore가 따른다."""

    secret_key: str

    def create_reservation(
        self,
        *,
        email: str,
        query: str,
        places: list[AlertPlace],
        radius_m: int,
        ip_hash: str | None,
        link_nonce: str,
        expires_at: datetime,
    ) -> dict[str, Any]: ...

    def discard_reservation(self, reservation_id: int, ip_hash: str | None) -> None: ...

    def get_reservation(self, reservation_id: int) -> dict[str, Any] | None: ...

    def confirm_reservation(self, reservation_id: int) -> str: ...

    def extend_reservation(self, reservation_id: int) -> dict[str, Any]: ...

    def cancel_reservation(self, reservation_id: int) -> str: ...

    def message_items(self, reservation_id: int, message_id: int) -> list[dict[str, Any]] | None:
        """결과 메일 한 통이 알린 물품(기관이 연결된 것만). 그 예약의 결과 메일이 아니면 None."""
        ...

    def match_queue(self, item_source_code: str) -> list[dict[str, Any]]: ...

    def candidate_items(self, reservation_id: int, item_source_code: str) -> dict[str, Any]: ...

    def record_match(
        self,
        reservation_id: int,
        item_source_code: str,
        checked_until: str | None,
        items: list[tuple[int, float]],
    ) -> dict[str, Any]: ...

    def enqueue_expiry_notices(self) -> int: ...

    def claim_messages(
        self, *, limit: int, daily_cap: int, message_id: int | None = None
    ) -> list[dict[str, Any]]: ...

    def message_payload(self, message_id: int) -> dict[str, Any] | None: ...

    def finish_message(self, message_id: int, status: str, error: str | None = None) -> None: ...

    def delete_message(self, message_id: int) -> None: ...

    def close_ended_reservations(self) -> dict[str, Any]: ...

    def start_run(self, kind: str, item_source_code: str | None = None) -> int: ...

    def finish_run(self, run_id: int, values: dict[str, Any]) -> None: ...


class SupabaseAlertStore(SupabaseRestClient):
    """서버 전용 secret key로 알림 테이블·DB 함수를 부른다(절대 규칙 4·6). 요청 코드는 수집 저장소와 같이 쓴다."""

    error_label = "Supabase 알림 API"

    def __init__(self, url: str, secret_key: str, *, timeout: float = 20.0) -> None:
        self.url = url.rstrip("/")
        self.secret_key = secret_key.strip()
        self.timeout = timeout
        if not self.url or not self.secret_key:
            raise ValueError("Supabase URL과 서버 전용 secret key가 필요합니다.")

    @classmethod
    def from_environment(cls) -> SupabaseAlertStore:
        secret_key = read_optional_env_value("SUPABASE_SECRET_KEY") or read_optional_env_value(
            "SUPABASE_SERVICE_ROLE_KEY"
        )
        if not secret_key:
            raise ValueError("SUPABASE_SECRET_KEY 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다.")
        return cls(read_env_value("SUPABASE_URL"), secret_key)

    def _rpc(self, name: str, args: dict[str, Any]) -> Any:
        return self._request("POST", f"rpc/{name}", payload=args)

    def create_reservation(
        self,
        *,
        email: str,
        query: str,
        places: list[AlertPlace],
        radius_m: int,
        ip_hash: str | None,
        link_nonce: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        return dict(
            self._rpc(
                "create_alert_reservation",
                {
                    "p_email": email,
                    "p_query": query,
                    "p_places": [
                        {"place_name": place.name, "latitude": place.latitude, "longitude": place.longitude}
                        for place in places
                    ],
                    "p_radius_m": radius_m,
                    "p_ip_hash": ip_hash,
                    "p_link_nonce": link_nonce,
                    "p_expires_at": expires_at.isoformat(),
                },
            )
        )

    def discard_reservation(self, reservation_id: int, ip_hash: str | None) -> None:
        # 리뷰 2026-11-15 — 실패한 예약은 IP 하루 5건 기록도 함께 지운다(같은 DB 함수 안에서).
        self._rpc("discard_alert_reservation", {"p_reservation_id": int(reservation_id), "p_ip_hash": ip_hash})

    def get_reservation(self, reservation_id: int) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "alert_reservations",
            query={"select": RESERVATION_FIELDS, "id": f"eq.{int(reservation_id)}"},
        )
        if not isinstance(rows, list):
            raise RuntimeError("Supabase 알림 API 응답 형식이 올바르지 않습니다.")
        if not rows:
            return None
        return {**rows[0], "places": _ordered_places(rows[0].get("places"))}

    def confirm_reservation(self, reservation_id: int) -> str:
        return str(self._rpc("confirm_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def extend_reservation(self, reservation_id: int) -> dict[str, Any]:
        return dict(self._rpc("extend_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def cancel_reservation(self, reservation_id: int) -> str:
        return str(self._rpc("cancel_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def message_items(self, reservation_id: int, message_id: int) -> list[dict[str, Any]] | None:
        """PRD 4.3-E — 메일 링크 화면(2026-11-24). 읽기만 하며 새 DB 함수 없이 기존 표를 본다.

        메일 번호가 이 예약의 결과 메일(match·expiry_notice)인지 먼저 확인한다(남의 메일 번호 차단).
        기관이 끊긴 물품은 빼고 돌려준다(AGENTS.md 절대 규칙 5).
        """
        scope = {"reservation_id": f"eq.{int(reservation_id)}"}
        messages = self._request(
            "GET",
            "alert_messages",
            query={"select": "id", "id": f"eq.{int(message_id)}", "kind": "in.(match,expiry_notice)", **scope},
        )
        if not isinstance(messages, list):
            raise RuntimeError("Supabase 알림 API 응답 형식이 올바르지 않습니다.")
        if not messages:
            return None
        notes = self._request(
            "GET",
            "alert_notified_items",
            query={"select": "found_item_id,score", "message_id": f"eq.{int(message_id)}", **scope},
        )
        if not isinstance(notes, list):
            raise RuntimeError("Supabase 알림 API 응답 형식이 올바르지 않습니다.")
        scores = {int(note["found_item_id"]): note.get("score") for note in notes}
        if not scores:
            return []
        rows = self._request(
            "GET",
            "found_items",
            query={
                "select": SupabaseFoundItemClient.SELECT_FIELDS,
                "id": f"in.({','.join(str(item_id) for item_id in sorted(scores))})",
                "storage_location_id": "not.is.null",
            },
        )
        if not isinstance(rows, list):
            raise RuntimeError("Supabase 알림 API 응답 형식이 올바르지 않습니다.")
        return [{**row, "score": scores.get(int(row["id"]))} for row in rows if isinstance(row, dict)]

    def match_queue(self, item_source_code: str) -> list[dict[str, Any]]:
        return list(self._rpc("alert_match_queue", {"p_item_source_code": item_source_code}) or [])

    def candidate_items(self, reservation_id: int, item_source_code: str) -> dict[str, Any]:
        payload = self._rpc(
            "alert_candidate_items",
            {"p_reservation_id": int(reservation_id), "p_item_source_code": item_source_code},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("Supabase 알림 후보 응답 형식이 올바르지 않습니다.")
        return payload

    def record_match(
        self,
        reservation_id: int,
        item_source_code: str,
        checked_until: str | None,
        items: list[tuple[int, float]],
    ) -> dict[str, Any]:
        return dict(
            self._rpc(
                "record_alert_match",
                {
                    "p_reservation_id": int(reservation_id),
                    "p_item_source_code": item_source_code,
                    "p_checked_until": checked_until,
                    "p_items": [{"found_item_id": item_id, "score": score} for item_id, score in items],
                },
            )
        )

    def enqueue_expiry_notices(self) -> int:
        return int(self._rpc("enqueue_alert_expiry_notices", {}) or 0)

    def claim_messages(
        self, *, limit: int, daily_cap: int, message_id: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self._rpc(
            "claim_alert_messages",
            {"p_limit": int(limit), "p_daily_cap": int(daily_cap), "p_message_id": message_id},
        )
        return list(rows or [])

    def message_payload(self, message_id: int) -> dict[str, Any] | None:
        payload = self._rpc("alert_message_payload", {"p_message_id": int(message_id)})
        return payload if isinstance(payload, dict) else None

    def finish_message(self, message_id: int, status: str, error: str | None = None) -> None:
        self._rpc(
            "finish_alert_message",
            {"p_message_id": int(message_id), "p_status": status, "p_error": error},
        )

    def delete_message(self, message_id: int) -> None:
        self._request(
            "DELETE",
            "alert_messages",
            query={"id": f"eq.{int(message_id)}"},
            prefer="return=minimal",
        )

    def close_ended_reservations(self) -> dict[str, Any]:
        return dict(self._rpc("close_ended_alert_reservations", {}) or {})

    def start_run(self, kind: str, item_source_code: str | None = None) -> int:
        rows = self._request(
            "POST",
            "alert_runs",
            payload={"kind": kind, "item_source_code": item_source_code},
            prefer="return=representation",
        )
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("알림 실행 기록을 만들지 못했습니다.")
        return int(rows[0]["id"])

    def finish_run(self, run_id: int, values: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            "alert_runs",
            query={"id": f"eq.{int(run_id)}"},
            payload={**values, "finished_at": datetime.now(timezone.utc).isoformat()},
            prefer="return=minimal",
        )
