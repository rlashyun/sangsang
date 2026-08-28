from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..config import read_env_value, read_optional_env_value


SOURCE_LOCATION_CODES = {
    "partner_api": "partner_csv",
    "police_api": "esri_police",
}
UPSERT_BATCH_SIZE = 250


class RunAlreadyActive(RuntimeError):
    """같은 출처의 동기화가 이미 실행 중입니다."""


class SupabaseRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class LocationReference:
    id: int
    name: str
    normalized_name: str


class SupabaseIngestionStore:
    """service-role 전용 Supabase Data API 저장소입니다."""

    def __init__(self, url: str, secret_key: str, *, timeout: float = 30.0) -> None:
        if not url or not secret_key:
            raise ValueError("Supabase URL과 비밀키가 필요합니다.")
        self.url = url.rstrip("/")
        self.secret_key = secret_key
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> SupabaseIngestionStore:
        url = read_env_value("SUPABASE_URL").rstrip("/")
        secret_key = read_optional_env_value(
            "SUPABASE_SECRET_KEY"
        ) or read_optional_env_value("SUPABASE_SERVICE_ROLE_KEY")
        if not secret_key:
            raise ValueError(
                "SUPABASE_SECRET_KEY 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다."
            )
        return cls(url, secret_key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: list[tuple[str, str]] | None = None,
        payload: Any | None = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.url}/rest/v1/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "User-Agent": "RetrieverLostFound/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = response.read()
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")[:1000]
            response_body = response_body.replace(self.secret_key, "[REDACTED]")
            raise SupabaseRequestError(
                f"Supabase Data API HTTP {error.code}: {response_body or error.reason}",
                status_code=error.code,
            ) from None
        except (urllib.error.URLError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            raise SupabaseRequestError(f"Supabase Data API 연결 실패: {reason}") from None
        if not response_body:
            return None
        try:
            return json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SupabaseRequestError("Supabase Data API 응답이 JSON이 아닙니다.") from error

    def _fetch_all(
        self,
        table: str,
        select: str,
        *,
        filters: list[tuple[str, str]] | None = None,
        page_size: int = 1_000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._request(
                "GET",
                table,
                query=[
                    ("select", select),
                    *(filters or []),
                    ("limit", str(page_size)),
                    ("offset", str(offset)),
                ],
            )
            if not isinstance(page, list):
                raise SupabaseRequestError(f"{table} 조회 응답 형식이 올바르지 않습니다.")
            rows.extend(page)
            if len(page) < page_size:
                return rows
            offset += page_size

    def create_run(
        self,
        source_code: str,
        start_date: date,
        end_date: date,
        *,
        trigger: str,
    ) -> int:
        stale_before = datetime.now(timezone.utc) - timedelta(hours=2)
        self._request(
            "PATCH",
            "ingestion_runs",
            query=[
                ("item_source_code", f"eq.{source_code}"),
                ("status", "eq.running"),
                ("started_at", f"lt.{stale_before.isoformat()}"),
            ],
            payload={
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error_message": "stale running lock released after 2 hours",
            },
            prefer="return=minimal",
        )
        payload = {
            "item_source_code": source_code,
            "status": "running",
            "metadata": {
                "window_start": start_date.isoformat(),
                "window_end": end_date.isoformat(),
                "trigger": trigger,
            },
        }
        try:
            result = self._request(
                "POST",
                "ingestion_runs",
                payload=payload,
                prefer="return=representation",
            )
        except SupabaseRequestError as error:
            if error.status_code == 409 and (
                "23505" in str(error)
                or "ingestion_runs_one_running_per_source" in str(error)
            ):
                raise RunAlreadyActive(source_code) from None
            raise
        if not isinstance(result, list) or not result or "id" not in result[0]:
            raise SupabaseRequestError("ingestion_runs 시작 기록을 확인하지 못했습니다.")
        return int(result[0]["id"])

    def finish_run(self, run_id: int, values: dict[str, Any]) -> None:
        payload = {**values, "finished_at": datetime.now(timezone.utc).isoformat()}
        result = self._request(
            "PATCH",
            "ingestion_runs",
            query=[("id", f"eq.{run_id}"), ("status", "eq.running")],
            payload=payload,
            prefer="return=representation",
        )
        if not isinstance(result, list) or len(result) != 1:
            raise SupabaseRequestError("ingestion_runs 종료 기록을 갱신하지 못했습니다.")

    def load_category_ids(self, source_code: str) -> dict[str, int]:
        rows = self._fetch_all(
            "category_mappings",
            "normalized_raw_category,category_id",
            filters=[
                ("item_source_code", f"eq.{source_code}"),
                ("is_active", "eq.true"),
            ],
        )
        return {
            str(row["normalized_raw_category"]): int(row["category_id"])
            for row in rows
        }

    def load_aliases(self, source_code: str) -> dict[str, int]:
        rows = self._fetch_all(
            "storage_location_aliases",
            "normalized_alias,storage_location_id,region_code",
            filters=[("item_source_code", f"eq.{source_code}")],
        )
        return {
            str(row["normalized_alias"]): int(row["storage_location_id"])
            for row in rows
            if not str(row.get("region_code") or "")
        }

    def load_locations(self, source_code: str) -> list[LocationReference]:
        location_source_code = SOURCE_LOCATION_CODES[source_code]
        rows = self._fetch_all(
            "storage_locations",
            "id,name,normalized_name",
            filters=[
                ("location_source_code", f"eq.{location_source_code}"),
                ("is_active", "eq.true"),
            ],
        )
        return [
            LocationReference(
                id=int(row["id"]),
                name=str(row["name"]),
                normalized_name=str(row["normalized_name"]),
            )
            for row in rows
        ]

    def existing_identities(self, source_code: str) -> set[tuple[str, str]]:
        rows = self._fetch_all(
            "found_items",
            "atc_id,found_sequence",
            filters=[("item_source_code", f"eq.{source_code}")],
        )
        return {(str(row["atc_id"]), str(row["found_sequence"])) for row in rows}

    def latest_registered_on(self, source_code: str) -> date | None:
        rows = self._request(
            "GET",
            "found_items",
            query=[
                ("select", "registered_on"),
                ("item_source_code", f"eq.{source_code}"),
                ("order", "registered_on.desc"),
                ("limit", "1"),
            ],
        )
        if not isinstance(rows, list):
            raise SupabaseRequestError("found_items 최신 등록일 응답 형식이 올바르지 않습니다.")
        if not rows:
            return None
        return date.fromisoformat(str(rows[0]["registered_on"]))

    def upsert_found_items(self, rows: list[dict[str, Any]]) -> None:
        for batch in chunks(rows, UPSERT_BATCH_SIZE):
            self._request(
                "POST",
                "found_items",
                query=[("on_conflict", "item_source_code,atc_id,found_sequence")],
                payload=batch,
                prefer="resolution=merge-duplicates,return=minimal",
            )

    def delete_expired(self, source_code: str) -> int:
        result = self._request(
            "POST",
            "rpc/delete_expired_found_items",
            payload={"p_source_code": source_code},
        )
        if not isinstance(result, list) or len(result) != 1:
            raise SupabaseRequestError("만료 데이터 삭제 결과를 확인하지 못했습니다.")
        return int(result[0].get("deleted_count", 0))


def chunks(
    values: list[dict[str, Any]], size: int
) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
