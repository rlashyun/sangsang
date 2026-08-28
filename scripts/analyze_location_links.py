from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from retriever_lost_found.ingestion.store import (
    SOURCE_LOCATION_CODES,
    SupabaseIngestionStore,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
CSV_PATH = REPORT_DIR / "unresolved_location_mapping_2026-08-29.csv"
SUMMARY_PATH = REPORT_DIR / "unresolved_location_summary_2026-08-29.json"

# 2026-06-30 경찰청 공식 관서 목록의 현행 명칭과 DB 좌표 원장의 동일 주소를
# 대조해 확인한 명칭 변경/원장 결합 오류입니다.
VERIFIED_POLICE_RENAMES = {
    "수원장안경찰서": 2845,  # DB: 수원중부경찰서
    "수원영통경찰서": 2846,  # DB: 수원남부경찰서
    "수원권선경찰서": 2847,  # DB: 수원서부경찰서
    "광주경찰서": 2868,      # DB 오류: 광주의왕경찰서
}


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def base_name(value: object) -> str:
    return normalize(re.sub(r"\([^)]*\)|\[[^]]*\]", "", str(value or "")))


def agency_key(atc_id: object) -> str:
    match = re.match(r"^(.+?)L\d{8}", str(atc_id or ""))
    return match.group(1) if match else ""


def location_kind(value: object) -> str:
    normalized = normalize(value)
    for suffix in (
        "총영사관", "민원봉사실", "교통정보센터", "유실물센터", "경찰대",
        "치안센터", "경찰서", "지구대", "파출소", "대사관", "카지노", "역",
    ):
        if normalized.endswith(suffix):
            return suffix
    return ""


def candidate_score(raw_name: str, location: dict[str, object]) -> float:
    raw = normalize(raw_name)
    raw_base = base_name(raw_name)
    names = {
        normalize(location.get("name")),
        normalize(location.get("normalized_name")),
        base_name(location.get("name")),
    } - {""}
    score = max(SequenceMatcher(None, raw, name).ratio() for name in names)
    if raw in names:
        score = 1.0
    elif raw_base in names:
        score = max(score, 0.97)
    elif any(raw.endswith(name) or name.endswith(raw) for name in names):
        score = max(score, 0.92)
    elif any(raw_base.endswith(name) or name.endswith(raw_base) for name in names):
        score = max(score, 0.88)
    raw_kind = location_kind(raw_name)
    candidate_kind = location_kind(location.get("name"))
    if raw_kind and candidate_kind and raw_kind != candidate_kind:
        score -= 0.12
    return round(max(0.0, min(1.0, score)), 4)


def compact_candidate(location: dict[str, object], score: float) -> str:
    return " | ".join(
        (
            str(location["id"]),
            str(location["name"]),
            str(location.get("address") or ""),
            str(location.get("region_code") or ""),
            f"{score:.4f}",
        )
    )


def main() -> int:
    store = SupabaseIngestionStore.from_environment()
    items = store._fetch_all(
        "found_items",
        "item_source_code,atc_id,raw_storage_name,location_match_status,storage_location_id,registered_on",
    )
    aliases = store._fetch_all(
        "storage_location_aliases",
        "item_source_code,normalized_alias,storage_location_id,region_code",
    )
    locations = store._fetch_all(
        "storage_locations",
        "id,location_source_code,source_key,name,normalized_name,location_kind,address,region_code,is_active",
        filters=[("is_active", "eq.true")],
    )

    aliases_by_source = defaultdict(dict)
    for alias in aliases:
        if not str(alias.get("region_code") or ""):
            aliases_by_source[str(alias["item_source_code"])][str(alias["normalized_alias"])] = int(alias["storage_location_id"])

    locations_by_source = defaultdict(list)
    locations_by_id = {int(location["id"]): location for location in locations}
    for source_code, location_source_code in SOURCE_LOCATION_CODES.items():
        locations_by_source[source_code] = [
            location
            for location in locations
            if location["location_source_code"] == location_source_code
        ]

    matched_partner_by_agency = defaultdict(set)
    for item in items:
        if item["item_source_code"] != "partner_api" or not item.get("storage_location_id"):
            continue
        key = agency_key(item["atc_id"])
        if key:
            matched_partner_by_agency[key].add(int(item["storage_location_id"]))

    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for item in items:
        if item["location_match_status"] != "matched":
            grouped[(str(item["item_source_code"]), str(item["raw_storage_name"]), str(item["location_match_status"]))].append(item)

    output_rows = []
    for (source_code, raw_name, current_status), group in grouped.items():
        scoped_locations = locations_by_source[source_code]
        raw_normalized = normalize(raw_name)
        raw_base = base_name(raw_name)
        exact = [
            location for location in scoped_locations
            if raw_normalized in {
                normalize(location.get("name")),
                normalize(location.get("normalized_name")),
                base_name(location.get("name")),
            }
        ]
        suffix = [
            location for location in scoped_locations
            if normalize(location.get("normalized_name")).endswith(raw_normalized)
            or raw_normalized.endswith(normalize(location.get("normalized_name")))
        ]
        ranked = sorted(
            ((candidate_score(raw_name, location), location) for location in scoped_locations),
            key=lambda pair: (-pair[0], int(pair[1]["id"])),
        )
        top_score, top_location = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0

        keys = sorted({agency_key(item["atc_id"]) for item in group} - {""})
        agency_ids = sorted({
            location_id
            for key in keys
            for location_id in matched_partner_by_agency.get(key, set())
        })

        department_base = normalize(
            re.sub(r"(민원봉사실|교통정보센터)$", "", raw_name)
        )
        department_matches = [
            location for location in scoped_locations
            if department_base
            and (
                normalize(location.get("name")).endswith(department_base)
                or department_base.endswith(normalize(location.get("name")))
            )
        ]

        suggested_id: int | None = None
        cause = ""
        action = ""
        confidence = "none"
        evidence = ""
        if source_code == "police_api" and raw_name in VERIFIED_POLICE_RENAMES:
            suggested_id = VERIFIED_POLICE_RENAMES[raw_name]
            cause = "official_station_rename_or_reference_name_corruption"
            action = "add_verified_alias_refresh_canonical_name_and_backfill"
            confidence = "high"
            evidence = "police_directory_2026_06_30_and_same_address"
        elif len(agency_ids) == 1:
            suggested_id = agency_ids[0]
            cause = "stable_agency_key_not_used"
            action = "add_source_code_alias_and_match_by_agency_key"
            confidence = "high"
            evidence = "same_partner_agency_key_already_matched"
        elif len(department_matches) == 1:
            suggested_id = int(department_matches[0]["id"])
            cause = "department_suffix_not_normalized"
            action = "add_verified_alias_and_normalize_non_location_department_suffix"
            confidence = "high"
            evidence = "unique_station_after_department_suffix_removal"
        elif len(exact) > 1 or (current_status == "ambiguous" and len(suffix) > 1):
            cause = "duplicate_short_name_without_region"
            action = "collect_region_or_parent_station_then_add_region_scoped_alias"
            confidence = "blocked"
        elif len(exact) == 1:
            suggested_id = int(exact[0]["id"])
            cause = "resolver_or_alias_gap"
            action = "add_verified_global_alias_and_backfill"
            confidence = "high"
        elif raw_base != raw_normalized and sum(base_name(location.get("name")) == raw_base for location in scoped_locations) == 1:
            match = next(location for location in scoped_locations if base_name(location.get("name")) == raw_base)
            suggested_id = int(match["id"])
            cause = "parenthetical_qualifier_variant"
            action = "add_verified_alias_preserving_line_or_site_qualifier"
            confidence = "high"
        elif top_score >= 0.88 and top_score - second_score >= 0.06:
            suggested_id = int(top_location["id"])
            cause = "canonical_name_variant_or_department_suffix"
            action = "review_candidate_then_add_verified_alias_and_backfill"
            confidence = "medium"
        elif top_score >= 0.76:
            cause = "possible_name_variant_but_not_unique"
            action = "manual_review_with_address_or_parent_station"
            confidence = "low"
        elif re.search(r"유실물센터|콜센터|대사관|총영사관|경찰대", raw_name):
            cause = "special_location_missing_from_coordinate_reference"
            action = "create_and_geocode_storage_location_then_add_alias"
            confidence = "none"
        else:
            cause = "location_missing_or_reference_dataset_stale"
            action = "verify_current_facility_create_location_geocode_and_add_alias"
            confidence = "none"

        candidate_pool = exact or suffix or [location for _, location in ranked[:5]]
        candidate_rows = []
        for location in candidate_pool[:10]:
            score = candidate_score(raw_name, location)
            candidate_rows.append(compact_candidate(location, score))

        suggested = locations_by_id.get(suggested_id) if suggested_id else None
        output_rows.append({
            "source": source_code,
            "dep_place": raw_name,
            "current_status": current_status,
            "item_count": len(group),
            "first_date": min(str(item["registered_on"]) for item in group),
            "last_date": max(str(item["registered_on"]) for item in group),
            "normalized_dep_place": raw_normalized,
            "agency_keys": ";".join(keys),
            "cause": cause,
            "solution": action,
            "confidence": confidence,
            "evidence": evidence,
            "suggested_location_id": suggested_id or "",
            "suggested_location_name": suggested.get("name", "") if suggested else "",
            "suggested_location_address": suggested.get("address", "") if suggested else "",
            "candidate_count_current_rule": len(set(int(location["id"]) for location in (exact or suffix))),
            "candidates_id_name_address_region_score": " || ".join(candidate_rows),
        })

    output_rows.sort(key=lambda row: (row["source"], -int(row["item_count"]), row["dep_place"]))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=output_rows[0].keys())
        writer.writeheader()
        writer.writerows(output_rows)

    source_counts = Counter(item["item_source_code"] for item in items)
    matched_counts = Counter(item["item_source_code"] for item in items if item["location_match_status"] == "matched")
    unresolved_counts = Counter(item["item_source_code"] for item in items if item["location_match_status"] != "matched")
    summary = {
        "snapshot": "2026-08-29",
        "total_items": len(items),
        "locations": len(locations),
        "aliases": len(aliases),
        "unresolved_items": sum(unresolved_counts.values()),
        "unresolved_distinct_dep_place": len(output_rows),
        "by_source": {
            source: {
                "total": source_counts[source],
                "matched": matched_counts[source],
                "unresolved": unresolved_counts[source],
                "match_rate_pct": round(100 * matched_counts[source] / source_counts[source], 2),
            }
            for source in sorted(source_counts)
        },
        "by_cause_distinct_names": dict(Counter(row["cause"] for row in output_rows)),
        "by_solution_distinct_names": dict(Counter(row["solution"] for row in output_rows)),
        "recoverable_items_by_confidence": {
            confidence: sum(int(row["item_count"]) for row in output_rows if row["confidence"] == confidence)
            for confidence in ("high", "medium", "low", "blocked", "none")
        },
        "csv": str(CSV_PATH),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
