"""Read-only institution lookup using its saved Kakao search query.

Usage: uv run python scripts/search_storage_location_kakao.py <location_id>
Apply the add_storage_location_kakao_search migration first. Populate
storage_locations.kakao_search with a researched query in the DB editor.
Results are candidates for review; this command never changes coordinates,
institution names, mappings, or found items.
"""

from __future__ import annotations

import argparse
import json

from retriever_lost_found.config import read_env_value
from retriever_lost_found.ingestion.store import SupabaseIngestionStore
from retriever_lost_found.integrations.kakao import KakaoLocalClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location_id", type=int)
    args = parser.parse_args()
    try:
        store = SupabaseIngestionStore.from_environment()
        rows = store._request(
            "GET", "storage_locations", query=[
                ("select", "id,name,kakao_search"),
                ("id", f"eq.{args.location_id}"),
                ("is_active", "eq.true"),
                ("limit", "1"),
            ],
        )
        if not rows:
            print("활성 기관을 찾지 못했어.")
            return 1
        location = rows[0]
        # PRD 4.5 — search wording never changes institution matching keys.
        query = location.get("kakao_search") or location["name"]
        result = KakaoLocalClient(read_env_value("KAKAO_REST_API_KEY")).search_keyword(query)
        print(json.dumps({
            "location_id": location["id"], "name": location["name"],
            "query": query, "candidates": result.get("documents", []),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # Configuration and network errors may contain secret values or URLs.
        print(f"기관 검색 실패 ({type(error).__name__}). 연결 및 컬럼 적용 여부를 확인해 줘.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
