"""Esri 경찰관서 레이어 -> storage_locations(esri_police) upsert SQL. PRD 4.5 / 6."""
import re, sys, unicodedata
from sync_police_locations import fetch_layer, normalize_features

def key(v):
    n = unicodedata.normalize("NFKC", str(v or "")).casefold()
    return "".join(c for c in n if c.isalnum())

def q(v):
    return "'" + str(v).replace("'", "''") + "'"

out = ["begin;"]
counts = {}
for layer, prefix, kind in ((0, "station", "경찰서"), (1, "substation", "지역경찰관서")):
    rows = normalize_features(fetch_layer(layer), default_type=kind)
    counts[prefix] = len(rows)
    seen = set()
    for r in rows:
        k = f"{prefix}:{r['OBJECTID']}"
        if not r["기관명"] or k in seen:
            continue
        seen.add(k)
        out.append(
            "insert into public.storage_locations (location_source_code, source_key, name, normalized_name, location_kind, address, phone, position) values ("
            f"'esri_police', {q(k)}, {q(r['기관명'])}, {q(key(r['기관명']))}, {q(r['기관유형'])}, {q(r['주소'])}, {q(r['연락처'])}, "
            f"extensions.st_setsrid(extensions.st_makepoint({r['경도']}, {r['위도']}), 4326)::extensions.geography) "
            "on conflict (location_source_code, source_key) do update set name=excluded.name, normalized_name=excluded.normalized_name, "
            "location_kind=excluded.location_kind, address=excluded.address, phone=excluded.phone, position=excluded.position, is_active=true, updated_at=now();"
        )
        if k == "substation:164":
            print("검증 substation:164 =", r["기관명"], file=sys.stderr)
out.append("commit;")
open("police_locations.sql", "w", encoding="utf-8").write("\n".join(out) + "\n")
print(counts, file=sys.stderr)
