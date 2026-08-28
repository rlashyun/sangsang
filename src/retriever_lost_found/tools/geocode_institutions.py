from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path
from typing import Any

from ..config import read_env_value
from ..integrations.kakao import KakaoLocalClient


RESULT_COLUMNS = [
    "위도",
    "경도",
    "좌표변환상태",
    "좌표변환방식",
    "좌표품질",
    "매칭주소",
    "카카오장소ID",
]

REGION_ALIASES = {
    "서울특별시": "서울",
    "서울": "서울",
    "부산광역시": "부산",
    "부산": "부산",
    "대구광역시": "대구",
    "대구": "대구",
    "인천광역시": "인천",
    "인천시": "인천",
    "인천": "인천",
    "광주광역시": "광주",
    "광주": "광주",
    "대전광역시": "대전",
    "대전": "대전",
    "울산광역시": "울산",
    "울산": "울산",
    "세종특별자치시": "세종",
    "세종": "세종",
    "경기도": "경기",
    "경기": "경기",
    "강원특별자치도": "강원",
    "강원도": "강원",
    "강원": "강원",
    "충청북도": "충북",
    "충북": "충북",
    "충청남도": "충남",
    "충남": "충남",
    "전북특별자치도": "전북",
    "전라북도": "전북",
    "전북": "전북",
    "전라남도": "전남",
    "전남": "전남",
    "경상북도": "경북",
    "경북": "경북",
    "경상남도": "경남",
    "경남": "경남",
    "제주특별자치도": "제주",
    "제주": "제주",
}


def _address_candidates(address: str) -> list[str]:
    original = " ".join(address.split())
    without_parentheses = " ".join(re.sub(r"\([^)]*\)", " ", original).split())
    candidates = [original]
    if without_parentheses and without_parentheses != original:
        candidates.append(without_parentheses)
    return candidates


def _address_result(
    document: dict[str, Any], method: str, quality: str = "주소일치"
) -> dict[str, str]:
    road_address = document.get("road_address") or {}
    address = document.get("address") or {}
    matched_address = (
        road_address.get("address_name")
        or address.get("address_name")
        or document.get("address_name")
        or ""
    )
    return {
        "위도": str(document.get("y", "")),
        "경도": str(document.get("x", "")),
        "좌표변환상태": "성공",
        "좌표변환방식": method,
        "좌표품질": quality,
        "매칭주소": str(matched_address),
        "카카오장소ID": str(document.get("id", "")),
    }


def _region_hint(address: str) -> str:
    first_token = address.strip().split(maxsplit=1)[0] if address.strip() else ""
    return REGION_ALIASES.get(first_token, "")


def _document_region(document: dict[str, Any]) -> str:
    address = str(document.get("road_address_name") or document.get("address_name") or "")
    first_token = address.strip().split(maxsplit=1)[0] if address.strip() else ""
    return REGION_ALIASES.get(first_token, first_token)


def _keyword_candidates(row: dict[str, str], region: str) -> list[str]:
    institution = row.get("하위기관명", "").strip() or row.get("기관명", "").strip()
    without_parentheses = " ".join(re.sub(r"\([^)]*\)", " ", institution).split())
    parentheses = [part.strip() for part in re.findall(r"\(([^)]*)\)", institution) if part.strip()]
    names = [institution, without_parentheses, *parentheses]
    queries: list[str] = []
    for name in names:
        if not name:
            continue
        query = f"{region} {name}".strip()
        if query not in queries:
            queries.append(query)
    return queries


def geocode_row(client: KakaoLocalClient, row: dict[str, str]) -> dict[str, str]:
    address = row.get("주소", "").strip()
    if not address:
        return _failure("주소없음")

    for index, candidate in enumerate(_address_candidates(address)):
        payload = client.geocode_address(candidate, size=1)
        documents = payload.get("documents") or []
        if documents:
            method = "주소검색" if index == 0 else "주소검색_괄호제거"
            return _address_result(documents[0], method)

    region = _region_hint(address)
    for query in _keyword_candidates(row, region):
        payload = client.search_keyword(query, size=15)
        documents = payload.get("documents") or []
        matching = [document for document in documents if not region or _document_region(document) == region]
        if matching:
            return _address_result(
                matching[0], "키워드검색_지역일치", "기관명매칭_검토권장"
            )

    return _failure("검색결과없음")


def _failure(reason: str) -> dict[str, str]:
    return {
        "위도": "",
        "경도": "",
        "좌표변환상태": "실패",
        "좌표변환방식": reason,
        "좌표품질": "미변환",
        "매칭주소": "",
        "카카오장소ID": "",
    }


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "주소" not in reader.fieldnames:
            raise ValueError("입력 CSV에 '주소' 열이 필요합니다.")
        return list(reader.fieldnames), list(reader)


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def geocode_csv(
    input_path: Path,
    output_path: Path,
    client: KakaoLocalClient,
    *,
    checkpoint_every: int = 50,
    delay: float = 0.05,
) -> tuple[int, int]:
    source_columns, rows = _read_rows(input_path)
    output_columns = source_columns + [c for c in RESULT_COLUMNS if c not in source_columns]

    cache: dict[str, dict[str, str]] = {}
    if output_path.exists():
        _, previous_rows = _read_rows(output_path)
        for previous in previous_rows:
            if previous.get("좌표변환상태") == "성공":
                cache[previous.get("주소", "")] = {
                    column: previous.get(column, "") for column in RESULT_COLUMNS
                }

    completed = 0
    for index, row in enumerate(rows, start=1):
        address = row.get("주소", "")
        if address in cache:
            result = cache[address]
        else:
            for attempt in range(4):
                try:
                    result = geocode_row(client, row)
                    break
                except RuntimeError as error:
                    if attempt == 3:
                        result = _failure(f"API오류: {error}")
                        break
                    time.sleep(2**attempt)
            cache[address] = result
            if delay:
                time.sleep(delay)
        row.update(result)
        completed += 1
        if checkpoint_every and index % checkpoint_every == 0:
            _write_rows(output_path, output_columns, rows)
            print(f"진행: {index}/{len(rows)}", flush=True)

    _write_rows(output_path, output_columns, rows)
    succeeded = sum(row.get("좌표변환상태") == "성공" for row in rows)
    return succeeded, completed - succeeded


def main() -> None:
    parser = argparse.ArgumentParser(description="기관 주소를 카카오 좌표로 일괄 변환합니다.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--delay", type=float, default=0.05)
    args = parser.parse_args()

    key = read_env_value("KAKAO_REST_API_KEY", args.env)
    succeeded, failed = geocode_csv(
        args.input,
        args.output,
        KakaoLocalClient(key),
        delay=max(0.0, args.delay),
    )
    print(f"완료: 성공 {succeeded}건, 실패 {failed}건, 출력 {args.output}")


if __name__ == "__main__":
    main()
