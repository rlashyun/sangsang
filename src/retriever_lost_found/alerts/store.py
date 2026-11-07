from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Protocol

from ..config import read_env_value, read_optional_env_value

RESERVATION_FIELDS = "id,email,query,place_name,radius_m,status,link_nonce,created_at,expires_at,extension_count"


class AlertStore(Protocol):
    """설계 4.6 — 알림 예약 저장소. SupabaseAlertStore와 테스트의 FakeAlertStore가 따른다."""

    secret_key: str

    def create_reservation(
        self,
        *,
        email: str,
        query: str,
        place_name: str,
        latitude: float,
        longitude: float,
        radius_m: int,
        ip_hash: str | None,
        link_nonce: str,
        expires_at: datetime,
    ) -> dict[str, Any]: ...

    def delete_reservation(self, reservation_id: int) -> None: ...

    def get_reservation(self, reservation_id: int) -> dict[str, Any] | None: ...

    def confirm_reservation(self, reservation_id: int) -> str: ...

    def extend_reservation(self, reservation_id: int) -> dict[str, Any]: ...

    def cancel_reservation(self, reservation_id: int) -> str: ...

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


class SupabaseAlertStore:
    """서버 전용 secret key로 알림 테이블·DB 함수를 부른다(절대 규칙 4·6)."""

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

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        payload: Any = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.url}/rest/v1/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
            "User-Agent": "RetrieverLostFound/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:240]
            detail = detail.replace(self.secret_key, "[REDACTED]")
            raise RuntimeError(f"Supabase 알림 API HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            raise RuntimeError(f"Supabase 알림 API 연결 실패: {reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise RuntimeError("Supabase 알림 API 응답이 올바른 JSON이 아닙니다.") from None

    def _rpc(self, name: str, args: dict[str, Any]) -> Any:
        return self._request("POST", f"rpc/{name}", payload=args)

    def create_reservation(
        self,
        *,
        email: str,
        query: str,
        place_name: str,
        latitude: float,
        longitude: float,
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
                    "p_place_name": place_name,
                    "p_latitude": latitude,
                    "p_longitude": longitude,
                    "p_radius_m": radius_m,
                    "p_ip_hash": ip_hash,
                    "p_link_nonce": link_nonce,
                    "p_expires_at": expires_at.isoformat(),
                },
            )
        )

    def delete_reservation(self, reservation_id: int) -> None:
        self._request(
            "DELETE",
            "alert_reservations",
            query={"id": f"eq.{int(reservation_id)}"},
            prefer="return=minimal",
        )

    def get_reservation(self, reservation_id: int) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "alert_reservations",
            query={"select": RESERVATION_FIELDS, "id": f"eq.{int(reservation_id)}"},
        )
        if not isinstance(rows, list):
            raise RuntimeError("Supabase 알림 API 응답 형식이 올바르지 않습니다.")
        return rows[0] if rows else None

    def confirm_reservation(self, reservation_id: int) -> str:
        return str(self._rpc("confirm_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def extend_reservation(self, reservation_id: int) -> dict[str, Any]:
        return dict(self._rpc("extend_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def cancel_reservation(self, reservation_id: int) -> str:
        return str(self._rpc("cancel_alert_reservation", {"p_reservation_id": int(reservation_id)}))

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
