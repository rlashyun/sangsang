"""Map provider data to the conversational search contracts."""

from typing import Any
from uuid import uuid4

from ..integrations.kakao import KakaoLocalClient
from .agent_search import Institution, PlaceCandidate, SearchPage


def adapt_places(client: KakaoLocalClient, query: str) -> list[PlaceCandidate]:
    # PRD 4.3 — Use provider coordinates and the existing address fallback.
    documents = client.search_keyword(query).get("documents", [])
    if not documents:
        documents = client.geocode_address(query).get("documents", [])
    candidates = []
    for document in documents:
        try:
            latitude, longitude = float(document["y"]), float(document["x"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        address = document.get("road_address_name") or document.get("address_name") or ""
        name = document.get("place_name") or address
        if name:
            candidates.append(PlaceCandidate(uuid4().hex, name, address, latitude, longitude))
    return candidates


def adapt_institutions(rows: list[dict[str, Any]]) -> list[Institution]:
    return [
        Institution(
            row["storage_location_id"], row["latitude"], row["longitude"],
            row.get("name") or "", row.get("address") or "", row.get("phone") or "",
        )
        for row in rows
    ]


def adapt_search_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in row.items() if key != "match_score"},
        "item_id": row["id"],
        "institution_id": row["storage_location_id"],
    }


def adapt_search_page(page: dict[str, Any]) -> SearchPage:
    items = tuple(adapt_search_item(row) for row in page["items"])
    return SearchPage(items, page["total_count"], page["next_cursor"])
