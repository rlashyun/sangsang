from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SUPABASE_RPC_PAGE_SIZE = 1_000
MAX_FOUND_ITEM_LOCATION_IDS = 300
FOUND_ITEM_PAGE_SIZE = 1_000


class SupabaseMapLocationClient:
    """서버 전용 Supabase RPC에서 지도 위치와 현재 물품 수를 함께 읽습니다."""

    def __init__(
        self,
        url: str,
        secret_key: str,
        *,
        timeout: float = 20.0,
        page_size: int = SUPABASE_RPC_PAGE_SIZE,
    ) -> None:
        self.url = url.rstrip("/")
        self.secret_key = secret_key.strip()
        self.timeout = timeout
        self.page_size = max(1, min(int(page_size), SUPABASE_RPC_PAGE_SIZE))
        if not self.url or not self.secret_key:
            raise ValueError("Supabase URL과 서버 전용 secret key가 필요합니다.")

    def fetch_locations(self) -> list[dict[str, Any]]:
        locations: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {
                    "order": "location_source_code.asc,source_key.asc",
                    "limit": self.page_size,
                    "offset": offset,
                }
            )
            request = urllib.request.Request(
                f"{self.url}/rest/v1/rpc/map_locations_with_item_counts?{query}",
                data=b"{}",
                method="POST",
                headers=_headers(self.secret_key, content_type=True),
            )
            payload = _request_json(request, self.timeout, "Supabase 지도 위치 API")
            if not isinstance(payload, list):
                raise RuntimeError("Supabase 지도 위치 API 응답 형식이 올바르지 않습니다.")
            for row in payload:
                location = map_location_from_rpc(row)
                if location is not None:
                    locations.append(location)
            if len(payload) < self.page_size:
                return locations
            offset += self.page_size


class SupabaseFoundItemClient:
    """선택된 보관기관의 습득물만 서버 전용 Data API로 조회합니다."""

    SELECT_FIELDS = ",".join(
        (
            "id",
            "storage_location_id",
            "item_source_code",
            "atc_id",
            "found_sequence",
            "raw_category_name",
            "color_name",
            "item_name",
            "description",
            "image_url",
            "raw_storage_name",
            "registered_on",
            "normalized_search_text",
        )
    )

    def __init__(self, url: str, secret_key: str, *, timeout: float = 20.0) -> None:
        self.url = url.rstrip("/")
        self.secret_key = secret_key.strip()
        self.timeout = timeout
        if not self.url or not self.secret_key:
            raise ValueError("Supabase URL과 서버 전용 secret key가 필요합니다.")

    def fetch_items(self, location_ids: list[int]) -> list[dict[str, Any]]:
        unique_ids = sorted(set(location_ids))
        if not unique_ids:
            return []
        if len(unique_ids) > MAX_FOUND_ITEM_LOCATION_IDS:
            raise ValueError(
                f"한 번에 조회할 수 있는 보관기관은 {MAX_FOUND_ITEM_LOCATION_IDS}곳까지입니다."
            )

        items: list[dict[str, Any]] = []
        offset = 0
        id_filter = f"in.({','.join(str(value) for value in unique_ids)})"
        while True:
            query = urllib.parse.urlencode(
                {
                    "select": self.SELECT_FIELDS,
                    "storage_location_id": id_filter,
                    "order": "registered_on.desc,id.desc",
                    "limit": FOUND_ITEM_PAGE_SIZE,
                    "offset": offset,
                }
            )
            request = urllib.request.Request(
                f"{self.url}/rest/v1/found_items?{query}",
                headers=_headers(self.secret_key),
            )
            payload = _request_json(request, self.timeout, "Supabase 물품 API")
            if not isinstance(payload, list):
                raise RuntimeError("Supabase 물품 API 응답 형식이 올바르지 않습니다.")
            items.extend(row for row in payload if isinstance(row, dict))
            if len(payload) < FOUND_ITEM_PAGE_SIZE:
                return items
            offset += FOUND_ITEM_PAGE_SIZE


def map_location_from_rpc(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    location_source_code = str(row.get("location_source_code", "")).strip()
    source_key = str(row.get("source_key", "")).strip()
    display_group = str(row.get("display_group", "")).strip()
    try:
        storage_location_id = int(row.get("id"))
        longitude = float(row.get("longitude"))
        latitude = float(row.get("latitude"))
        item_count = max(0, int(row.get("item_count", 0)))
    except (TypeError, ValueError):
        return None
    if (
        not location_source_code
        or not source_key
        or display_group not in {"partner", "police"}
        or storage_location_id <= 0
        or not (-180 <= longitude <= 180 and -90 <= latitude <= 90)
    ):
        return None
    return {
        "id": storage_location_id,
        "storage_location_id": storage_location_id,
        "organization": "경찰청" if display_group == "police" else "",
        "name": str(row.get("name", "")).strip(),
        "phone": str(row.get("phone", "") or "").strip(),
        "address": str(row.get("address", "") or "").strip(),
        "latitude": latitude,
        "longitude": longitude,
        "source": display_group,
        "location_source_code": location_source_code,
        "source_key": source_key,
        "item_count": item_count,
    }


def apply_item_counts(
    locations: list[dict[str, Any]],
    counts: dict[tuple[str, str], int],
    location_ids: dict[tuple[str, str], int] | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for location in locations:
        item = dict(location)
        location_key = (
            str(item.get("location_source_code", "")),
            str(item.get("source_key", "")),
        )
        item["item_count"] = counts.get(location_key, 0)
        item["storage_location_id"] = (location_ids or {}).get(location_key)
        enriched.append(item)
    return enriched


def validate_location_ids(raw_value: str) -> list[int]:
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    if not values:
        raise ValueError("조회할 보관기관이 없습니다.")
    if len(values) > MAX_FOUND_ITEM_LOCATION_IDS:
        raise ValueError(
            f"한 번에 조회할 수 있는 보관기관은 {MAX_FOUND_ITEM_LOCATION_IDS}곳까지입니다."
        )
    try:
        location_ids = [int(value) for value in values]
    except ValueError:
        raise ValueError("보관기관 ID 형식이 올바르지 않습니다.") from None
    if any(value <= 0 for value in location_ids):
        raise ValueError("보관기관 ID는 양수여야 합니다.")
    return sorted(set(location_ids))


def _headers(secret_key: str, *, content_type: bool = False) -> dict[str, str]:
    headers = {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
        "Accept": "application/json",
        "User-Agent": "RetrieverLostFound/0.1",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def _request_json(request: urllib.request.Request, timeout: float, label: str) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{label} HTTP {error.code}: {body[:240]}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"{label} 연결 실패: {error.reason}") from None
    except json.JSONDecodeError:
        raise RuntimeError(f"{label} 응답이 올바른 JSON이 아닙니다.") from None
