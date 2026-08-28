from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path


BASE_URL = (
    "https://apis.data.go.kr/1320000/LosPtfundInfoInqireService/"
    "getPtLosfundInfoAccToClAreaPd"
)


def normalize_service_key(value: str) -> str:
    """따옴표 및 Encoding/Decoding 키 형식 차이를 정규화합니다."""
    key = value.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
        key = key[1:-1].strip()
    return urllib.parse.unquote(key)


def read_service_key(env_path: Path = Path(".env")) -> str:
    """.env에서 공공데이터포털 API 키를 읽습니다."""
    env_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if env_key:
        return normalize_service_key(env_key)
    if not env_path.exists():
        raise ValueError(".env 파일이 없습니다.")
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("DATA_GO_KR_SERVICE_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return normalize_service_key(key)
            break
    raise ValueError(".env에 DATA_GO_KR_SERVICE_KEY를 입력하세요.")


def parse_xml(xml_data: bytes) -> tuple[list[dict[str, str]], int]:
    """XML 응답을 항목 목록과 전체 건수로 변환합니다."""
    root = ET.fromstring(xml_data)
    header = root.find("header")
    if header is not None:
        result_code = (header.findtext("resultCode") or "").strip()
        result_message = (header.findtext("resultMsg") or "").strip()
        if result_code and result_code != "00":
            raise ValueError(f"API 오류 {result_code}: {result_message}")
    body = root.find("body")
    if body is None:
        raise ValueError("API 응답에 body가 없습니다.")

    items = [
        {child.tag: (child.text or "").strip() for child in item}
        for item in body.findall("./items/item")
    ]
    total_count = int(body.findtext("totalCount", "0"))
    return items, total_count


class PoliceApiClient:
    """경찰청 포털기관 습득물 API 클라이언트입니다."""

    def __init__(self, service_key: str) -> None:
        self.service_key = service_key

    def fetch_page(
        self,
        page_no: int,
        num_of_rows: int = 100,
        **filters: str,
    ) -> tuple[list[dict[str, str]], int]:
        params = {
            "serviceKey": self.service_key,
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            **filters,
        }
        url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/xml",
                "User-Agent": "RetrieverLostFound/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return parse_xml(response.read())
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace").strip()
            response_body = response_body.replace(self.service_key, "[REDACTED]")
            if len(response_body) > 500:
                response_body = response_body[:500] + "..."
            detail = response_body or error.reason
            raise RuntimeError(f"경찰청 API HTTP {error.code}: {detail}") from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"경찰청 API 연결 실패: {error.reason}") from None

    def iter_pages(
        self,
        num_of_rows: int = 100,
        **filters: str,
    ) -> Iterator[list[dict[str, str]]]:
        """첫 페이지부터 마지막 페이지까지 순서대로 반환합니다."""
        page_no = 1
        while True:
            items, total_count = self.fetch_page(page_no, num_of_rows, **filters)
            if not items:
                return
            yield items
            if page_no * num_of_rows >= total_count:
                return
            page_no += 1


def create_client() -> PoliceApiClient:
    return PoliceApiClient(read_service_key())


def main() -> int:
    parser = argparse.ArgumentParser(description="경찰청 습득물 API 실데이터 확인")
    parser.add_argument("--start", required=True, help="조회 시작일 YYYYMMDD")
    parser.add_argument("--end", required=True, help="조회 종료일 YYYYMMDD")
    parser.add_argument("--rows", type=int, default=10, help="확인할 건수 (기본 10, 최대 100)")
    parser.add_argument("--location-code", help="경찰청 습득지역 코드")
    parser.add_argument("--category-large", help="경찰청 물품 대분류 코드")
    parser.add_argument("--category-middle", help="경찰청 물품 중분류 코드")
    parser.add_argument("--limit", type=int, help="중복을 제거해 수집할 전체 건수")
    parser.add_argument("--output", type=Path, help="전체 수집 결과를 저장할 JSONL 파일")
    args = parser.parse_args()

    if len(args.start) != 8 or not args.start.isdigit():
        parser.error("--start는 YYYYMMDD 형식이어야 합니다.")
    if len(args.end) != 8 or not args.end.isdigit():
        parser.error("--end는 YYYYMMDD 형식이어야 합니다.")
    if not 1 <= args.rows <= 100:
        parser.error("--rows는 1~100 범위여야 합니다.")
    if args.limit is not None and not 1 <= args.limit <= 10000:
        parser.error("--limit는 1~10000 범위여야 합니다.")
    if args.limit is not None and args.output is None:
        parser.error("전체 수집에는 --output JSONL 파일을 지정해야 합니다.")

    filters = {"START_YMD": args.start, "END_YMD": args.end}
    if args.location_code:
        filters["N_FD_LCT_CD"] = args.location_code
    if args.category_large:
        filters["PRDT_CL_CD_01"] = args.category_large
    if args.category_middle:
        filters["PRDT_CL_CD_02"] = args.category_middle

    client = create_client()
    if args.limit is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        unique_items: dict[tuple[str, str], dict[str, str]] = {}
        page_count = 0
        for page in client.iter_pages(num_of_rows=args.rows, **filters):
            page_count += 1
            for item in page:
                key = (item.get("atcId", ""), item.get("fdSn", ""))
                if key not in unique_items:
                    unique_items[key] = item
                if len(unique_items) >= args.limit:
                    break
            if page_count % 10 == 0 or len(unique_items) >= args.limit:
                print(
                    f"수집 진행: {len(unique_items)}/{args.limit}건 ({page_count}페이지)",
                    file=sys.stderr,
                    flush=True,
                )
            if len(unique_items) >= args.limit:
                break
        with args.output.open("w", encoding="utf-8") as output:
            for item in unique_items.values():
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "collectedCount": len(unique_items),
                    "pageCount": page_count,
                    "output": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    items, total_count = client.fetch_page(page_no=1, num_of_rows=args.rows, **filters)
    print(
        json.dumps(
            {"totalCount": total_count, "returnedCount": len(items), "items": items},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
