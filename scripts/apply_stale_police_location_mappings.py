from __future__ import annotations

import re
from typing import Any

from retriever_lost_found.config import read_env_value
from retriever_lost_found.ingestion.store import SupabaseIngestionStore
from retriever_lost_found.integrations.kakao import KakaoLocalClient


EXISTING = {
    "송내공동체지역관서": (3687, 5),
    "지만파출소": (3604, 4),
    "성호여수지구대": (3621, 3),
    "팽성파출소": (3678, 3),
    "장안지구대": (3591, 2),
    "송하파출소": (4386, 2),
    "동백파출소": (3794, 2),
    "도량파출소": (4400, 1),
    "매교파출소": (3600, 1),
    "미사강변지구대": (3777, 1),
    "배곧파출소": (3765, 1),
    "송악파출소": (4071, 1),
    "쌍봉파출소": (4142, 1),
    "태화파출소": (4385, 1),
}

NEW_LOCATIONS = [
    ("수원팔달경찰서", "경기도 수원시 팔달구 세지로 387", "경기도", "경찰서", 95),
    ("인천영종경찰서", "인천광역시 영종구 하늘달빛로64번길 6-13", "인천광역시", "경찰서", 33),
    ("여름파출소(광안리해수욕장)", "광안리해수욕장 여름파출소", "부산광역시", "여름파출소", 9),
    ("용문지구대", "대전광역시 서구 계룡로 667", "대전광역시", "지구대", 6),
    ("여름파출소(을왕리)", "을왕리해수욕장 여름파출소", "인천광역시", "여름파출소", 3),
    ("여름파출소(해운대해수욕장)", "해운대해수욕장 여름파출소", "부산광역시", "여름파출소", 3),
    ("운정야당지구대", "경기도 파주시 한울로 55-1", "경기도", "지구대", 2),
    ("구포1치안센터", "부산광역시 북구 구포만세길 169", "부산광역시", "치안센터", 1),
    ("운정호수지구대", "경기도 파주시 해솔로 84", "경기도", "지구대", 1),
    ("이촌치안센터", "서울특별시 용산구 이촌로 219", "서울특별시", "치안센터", 1),
]

REGION_HINTS = {
    "서울특별시": "서울",
    "부산광역시": "부산",
    "대전광역시": "대전",
    "인천광역시": "인천",
    "경기도": "경기",
}


def normalize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


def geocode(client: KakaoLocalClient, query: str, region: str) -> dict[str, str]:
    documents = client.geocode_address(query, size=1).get("documents") or []
    if not documents:
        documents = client.search_keyword(query, size=15).get("documents") or []
    expected = REGION_HINTS[region]
    candidates = [
        row
        for row in documents
        if str(row.get("road_address_name") or row.get("address_name") or "").startswith(expected)
    ]
    if not candidates:
        fallback_query = query.replace(" 여름파출소", "")
        documents = client.search_keyword(fallback_query, size=15).get("documents") or []
        candidates = [
            row
            for row in documents
            if str(row.get("road_address_name") or row.get("address_name") or "").startswith(expected)
        ]
    if not candidates:
        raise RuntimeError(f"좌표 변환 실패 또는 지역 불일치: {query} ({region})")
    row = candidates[0]
    address = str(row.get("road_address_name") or row.get("address_name") or query)
    return {"address": address, "longitude": str(row["x"]), "latitude": str(row["y"])}


def main() -> None:
    store = SupabaseIngestionStore.from_environment()
    kakao = KakaoLocalClient(read_env_value("KAKAO_REST_API_KEY"))

    # 모든 좌표를 먼저 확정한다. 이 단계가 실패하면 DB 쓰기는 시작하지 않는다.
    resolved: dict[str, dict[str, str]] = {
        name: geocode(kakao, query, region)
        for name, query, region, _kind, _count in NEW_LOCATIONS
    }
    resolved["성호여수지구대"] = geocode(
        kakao, "성호여수지구대", "경기도"
    )

    new_rows: list[dict[str, Any]] = []
    for name, _query, region, kind, _count in NEW_LOCATIONS:
        point = resolved[name]
        new_rows.append(
            {
                "location_source_code": "esri_police",
                "source_key": f"manual:stale:{normalize(name)}",
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
        payload=new_rows,
        prefer="resolution=merge-duplicates,return=representation",
    ) or []
    new_ids = {str(row["normalized_name"]): int(row["id"]) for row in created}
    if len(new_ids) != len(NEW_LOCATIONS):
        raise RuntimeError(f"신규 장소 upsert 결과 불일치: {created!r}")

    moved = resolved["성호여수지구대"]
    store._request(
        "PATCH",
        "storage_locations",
        query=[("id", "eq.3621")],
        payload={
            "address": moved["address"],
            "position": f"POINT({moved['longitude']} {moved['latitude']})",
        },
        prefer="return=minimal",
    )

    mappings = dict(EXISTING)
    mappings.update(
        {
            name: (new_ids[normalize(name)], count)
            for name, _query, _region, _kind, count in NEW_LOCATIONS
        }
    )
    aliases = [
        {
            "item_source_code": "police_api",
            "alias_name": name,
            "normalized_alias": normalize(name),
            "region_code": "",
            "storage_location_id": location_id,
            "match_method": "manual",
            "verified": True,
        }
        for name, (location_id, _count) in mappings.items()
    ]
    store._request(
        "POST",
        "storage_location_aliases",
        query=[("on_conflict", "item_source_code,normalized_alias,region_code")],
        payload=aliases,
        prefer="resolution=merge-duplicates,return=minimal",
    )

    updated: list[tuple[str, int]] = []
    for name, (location_id, expected_count) in mappings.items():
        rows = store._request(
            "PATCH",
            "found_items",
            query=[
                ("item_source_code", "eq.police_api"),
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
        updated.append((name, len(rows)))

    print(
        {
            "created_locations": len(new_ids),
            "updated_location_coordinates": 1,
            "aliases": len(aliases),
            "items": sum(count for _name, count in updated),
            "coordinates": resolved,
        }
    )


if __name__ == "__main__":
    main()
