from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


BASE_URL = (
    "https://apis.data.go.kr/1320000/LosfundInfoInqireService/"
    "getLosfundInfoAccToClAreaPd"
)
DEFAULT_OUTPUT = Path("data/police-found-items-latest-10000.jsonl")


def normalize_service_key(value: str) -> str:
    key = value.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
        key = key[1:-1].strip()
    return urllib.parse.unquote(key)


def read_service_key(env_path: Path = Path(".env")) -> str:
    env_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if env_key:
        return normalize_service_key(env_key)

    if not env_path.exists():
        raise ValueError(f"API 키 파일을 찾을 수 없습니다: {env_path}")

    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == "DATA_GO_KR_SERVICE_KEY" and value.strip():
            return normalize_service_key(value)

    raise ValueError(
        f"{env_path}에 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다."
    )


def parse_response(xml_data: bytes) -> tuple[list[dict[str, str]], int]:
    root = ET.fromstring(xml_data)
    header = root.find("header")
    if header is None:
        raise ValueError("API 응답에 header가 없습니다.")

    result_code = (header.findtext("resultCode") or "").strip()
    result_message = (
        header.findtext("resultMsg")
        or header.findtext("resultMag")
        or ""
    ).strip()
    if result_code != "00":
        raise ValueError(f"API 오류 {result_code}: {result_message}")

    body = root.find("body")
    if body is None:
        raise ValueError("API 응답에 body가 없습니다.")

    items = [
        {child.tag: (child.text or "").strip() for child in item}
        for item in body.findall("./items/item")
    ]
    return items, int(body.findtext("totalCount", "0"))


class PoliceFoundItemsClient:
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
            "pageNo": str(page_no),
            "numOfRows": str(num_of_rows),
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
        retry_delays = (1, 2, 4, 8, 16)
        for attempt in range(len(retry_delays) + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return parse_response(response.read())
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                body = body.replace(self.service_key, "[REDACTED]")[:500]
                if error.code < 500 or attempt == len(retry_delays):
                    raise RuntimeError(
                        f"경찰청 습득물 API HTTP {error.code}: "
                        f"{body or error.reason}"
                    ) from None
                reason = f"HTTP {error.code}"
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == len(retry_delays):
                    reason = getattr(error, "reason", error)
                    raise RuntimeError(
                        f"경찰청 습득물 API 연결 실패: {reason}"
                    ) from None
                reason = getattr(error, "reason", error)

            delay = retry_delays[attempt]
            print(
                f"페이지 {page_no} 요청 실패({reason}), {delay}초 후 재시도 "
                f"({attempt + 1}/{len(retry_delays)})",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

        raise AssertionError("도달할 수 없는 코드")


def collect_latest(
    client: PoliceFoundItemsClient,
    *,
    limit: int,
    rows_per_page: int,
) -> list[dict[str, str]]:
    unique_items: dict[tuple[str, str], dict[str, str]] = {}
    page_no = 1
    total_count: int | None = None

    while len(unique_items) < limit:
        page_items, total_count = client.fetch_page(page_no, rows_per_page)
        if not page_items:
            break

        for item in page_items:
            key = (item.get("atcId", ""), item.get("fdSn", "1"))
            if not key[0]:
                continue
            unique_items.setdefault(key, item)
            if len(unique_items) >= limit:
                break

        print(
            f"수집 진행: {len(unique_items):,}/{limit:,}건 "
            f"({page_no:,}페이지, API 전체 {total_count:,}건)",
            file=sys.stderr,
            flush=True,
        )

        if page_no * rows_per_page >= total_count:
            break
        page_no += 1

    return list(unique_items.values())[:limit]


def write_jsonl(items: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for item in items:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="경찰청 습득물정보 API의 최신 데이터를 JSONL로 저장합니다."
    )
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit는 1 이상이어야 합니다.")
    if not 1 <= args.rows <= 100:
        parser.error("--rows는 1~100 범위여야 합니다.")

    client = PoliceFoundItemsClient(read_service_key(args.env_file))
    items = collect_latest(client, limit=args.limit, rows_per_page=args.rows)
    write_jsonl(items, args.output)

    print(
        json.dumps(
            {
                "requestedCount": args.limit,
                "savedCount": len(items),
                "output": str(args.output.resolve()),
                "firstItem": items[0] if items else None,
                "lastItem": items[-1] if items else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if len(items) == args.limit else 1


if __name__ == "__main__":
    raise SystemExit(main())
