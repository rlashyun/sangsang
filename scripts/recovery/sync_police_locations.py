from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


FEATURE_SERVER = (
    "https://portal.esrikr.com/arcgis/rest/services/Hosted/"
    "KR_PoliceStation/FeatureServer"
)
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DEFAULT_STATIONS_CSV = DEFAULT_DATA_DIR / "esri_police_stations.csv"
DEFAULT_SUBSTATIONS_CSV = DEFAULT_DATA_DIR / "esri_police_substations.csv"
CSV_FIELDS = [
    "OBJECTID",
    "기관유형",
    "시도경찰청",
    "기관명",
    "주소",
    "연락처",
    "위도",
    "경도",
    "데이터출처",
    "데이터기준일",
]


def fetch_layer(layer_id: int, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Esri Korea 경찰관서 레이어 전체를 WGS84 GeoJSON으로 조회합니다."""
    params = urllib.parse.urlencode(
        {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
        }
    )
    request = urllib.request.Request(
        f"{FEATURE_SERVER}/{layer_id}/query?{params}",
        headers={"Accept": "application/geo+json", "User-Agent": "RetrieverLostFound/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"Esri Layer {layer_id} 응답에 features가 없습니다.")
    return features


def normalize_features(
    features: list[dict[str, Any]], *, default_type: str
) -> list[dict[str, Any]]:
    """두 레이어를 동일한 CSV 스키마로 정규화합니다."""
    rows: list[dict[str, Any]] = []
    for feature in features:
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        longitude = properties.get("x")
        latitude = properties.get("y")
        if len(coordinates) >= 2:
            longitude, latitude = coordinates[:2]
        try:
            longitude = float(longitude)
            latitude = float(latitude)
        except (TypeError, ValueError):
            continue
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            continue
        rows.append(
            {
                "OBJECTID": properties.get("objectid", ""),
                "기관유형": str(properties.get("type") or default_type).strip(),
                "시도경찰청": str(properties.get("sido_npa") or "").strip(),
                "기관명": str(properties.get("name") or "").strip(),
                "주소": str(properties.get("address") or "").strip(),
                "연락처": str(properties.get("telephone") or "").strip(),
                "위도": f"{latitude:.8f}",
                "경도": f"{longitude:.8f}",
                "데이터출처": "Esri Korea KR_PoliceStation FeatureServer",
                "데이터기준일": "2024-12",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def download_police_locations(
    stations_path: Path = DEFAULT_STATIONS_CSV,
    substations_path: Path = DEFAULT_SUBSTATIONS_CSV,
) -> tuple[int, int]:
    stations = normalize_features(fetch_layer(0), default_type="경찰서")
    substations = normalize_features(fetch_layer(1), default_type="지역경찰관서")
    write_csv(stations_path, stations)
    write_csv(substations_path, substations)
    return len(stations), len(substations)


def main() -> int:
    parser = argparse.ArgumentParser(description="Esri Korea 전국 경찰관서 좌표 CSV 저장")
    parser.add_argument("--stations-output", type=Path, default=DEFAULT_STATIONS_CSV)
    parser.add_argument("--substations-output", type=Path, default=DEFAULT_SUBSTATIONS_CSV)
    args = parser.parse_args()
    station_count, substation_count = download_police_locations(
        args.stations_output,
        args.substations_output,
    )
    print(f"경찰서: {station_count:,}개 -> {args.stations_output}")
    print(f"지구대·파출소: {substation_count:,}개 -> {args.substations_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
