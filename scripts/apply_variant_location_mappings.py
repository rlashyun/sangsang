from __future__ import annotations

import re
from typing import Any

from retriever_lost_found.config import read_env_value
from retriever_lost_found.ingestion.store import SupabaseIngestionStore
from retriever_lost_found.integrations.kakao import KakaoLocalClient


EXISTING = {
    "인천제물포경찰서": (2817, 44, "police_api"),
    "신사우지구대": (4740, 3, "police_api"),
}

NEW_LOCATIONS = [
    (
        "부산수영경찰서",
        "부산광역시 수영구 수미로14번길 13",
        "부산광역시",
        "경찰서",
        "esri_police",
        "police_api",
        89,
    ),
    (
        "미사2지구대",
        "미사2지구대",
        "경기도",
        "지구대",
        "esri_police",
        "police_api",
        5,
    ),
    (
        "대일버스(주)",
        "부산광역시 영도구 조내기로 74",
        "부산광역시",
        "버스운송사업자",
        "partner_csv",
        "partner_api",
        4,
    ),
    (
        "광주경찰청",
        "광주경찰청",
        "광주광역시",
        "시도경찰청",
        "esri_police",
        "police_api",
        1,
    ),
]

REGION_HINTS = {
    "부산광역시": "부산",
    "경기도": "경기",
    "광주광역시": "광주",
}


def normalize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


def geocode(client: KakaoLocalClient, query: str, region: str) -> dict[str, str]:
    documents = client.geocode_address(query, size=1).get("documents") or []
    if not documents:
        documents = client.search_keyword(query, size=15).get("documents") or []
    expected = REGION_HINTS[region]
    candidates = []
    for row in documents:
        candidate_address = str(
            row.get("road_address_name") or row.get("address_name") or ""
        )
        # 2026년 통합 행정구역 표기(예: 전남광주통합특별시)도
        # 기존 원장의 광주광역시 지역 힌트와 동일 지역으로 검증한다.
        region_prefix = candidate_address.split(" ", 1)[0]
        if expected in region_prefix:
            candidates.append(row)
    if not candidates:
        raise RuntimeError(f"좌표 변환 실패 또는 지역 불일치: {query} ({region})")
    row = candidates[0]
    return {
        "address": str(row.get("road_address_name") or row.get("address_name") or query),
        "longitude": str(row["x"]),
        "latitude": str(row["y"]),
    }


def main() -> None:
    store = SupabaseIngestionStore.from_environment()
    kakao = KakaoLocalClient(read_env_value("KAKAO_REST_API_KEY"))

    # 모든 좌표를 먼저 확정한다. 실패 시 DB 쓰기를 시작하지 않는다.
    resolved = {
        name: geocode(kakao, query, region)
        for name, query, region, *_rest in NEW_LOCATIONS
    }

    payload: list[dict[str, Any]] = []
    for name, _query, region, kind, location_source, _item_source, _count in NEW_LOCATIONS:
        point = resolved[name]
        payload.append(
            {
                "location_source_code": location_source,
                "source_key": f"manual:variant:{normalize(name)}",
                "name": name,
                "normalized_name": normalize(name),
                "location_kind": kind,
                "address": point["address"],
                "region_code": region,
                "position": f"POINT({point['longitude']} {point['latitude']})",
                "is_active": True,
            }
        )
    created = store._request(
        "POST",
        "storage_locations",
        query=[("on_conflict", "location_source_code,source_key")],
        payload=payload,
        prefer="resolution=merge-duplicates,return=representation",
    ) or []
    new_ids = {str(row["normalized_name"]): int(row["id"]) for row in created}
    if len(new_ids) != len(NEW_LOCATIONS):
        raise RuntimeError(f"신규 장소 upsert 결과 불일치: {created!r}")

    mappings = dict(EXISTING)
    mappings.update(
        {
            name: (new_ids[normalize(name)], count, item_source)
            for name, _query, _region, _kind, _location_source, item_source, count
            in NEW_LOCATIONS
        }
    )
    aliases = [
        {
            "item_source_code": item_source,
            "alias_name": name,
            "normalized_alias": normalize(name),
            "region_code": "",
            "storage_location_id": location_id,
            "match_method": "manual",
            "verified": True,
        }
        for name, (location_id, _count, item_source) in mappings.items()
    ]
    store._request(
        "POST",
        "storage_location_aliases",
        query=[("on_conflict", "item_source_code,normalized_alias,region_code")],
        payload=aliases,
        prefer="resolution=merge-duplicates,return=minimal",
    )

    total = 0
    for name, (location_id, expected_count, item_source) in mappings.items():
        rows = store._request(
            "PATCH",
            "found_items",
            query=[
                ("item_source_code", f"eq.{item_source}"),
                ("location_match_status", "neq.matched"),
                ("raw_storage_name", f"eq.{name}"),
            ],
            payload={
                "storage_location_id": location_id,
                "location_match_status": "matched",
            },
            prefer="return=representation",
        ) or []
        if len(rows) != expected_count:
            raise RuntimeError(
                f"{name} 갱신 건수 불일치: expected={expected_count}, actual={len(rows)}"
            )
        total += len(rows)

    print(
        {
            "created_locations": len(new_ids),
            "aliases": len(aliases),
            "items": total,
            "coordinates": resolved,
        }
    )


if __name__ == "__main__":
    main()
