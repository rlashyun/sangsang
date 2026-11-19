from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from retriever_lost_found.ingestion.categories import (
    FALLBACK_CATEGORY,
    SERVICE_CATEGORIES,
    classify_product_category,
)
from retriever_lost_found.ingestion.collector import (
    collect_selected_rows,
    write_csv,
)


class CategoryClassificationTests(unittest.TestCase):
    def test_original_six_categories_keep_their_codes(self) -> None:
        expected = {
            "가방 > 여성용가방": "bag",
            "가방 > 남성용가방": "bag",
            "가방 > 기타가방": "bag",
            "의류 > 모자": "clothing",
            "전자기기 > 무선이어폰": "electronics",
            "지갑 > 남성용 지갑": "wallet",
            "지갑 > 여성용 지갑": "wallet",
            "지갑 > 기타 지갑": "wallet",
            "휴대폰 > 삼성휴대폰": "mobile_phone",
            "휴대폰 > LG휴대폰": "mobile_phone",
            "휴대폰 > 아이폰": "mobile_phone",
            "휴대폰 > 기타휴대폰": "mobile_phone",
            "휴대폰 > 기타통신기기": "mobile_phone",
            "카드 > 신용(체크)카드": "card",
            "카드 > 일반카드": "card",
            "카드 > 교통카드": "card",
            "카드 > 기타카드": "card",
        }
        for raw_category, expected_code in expected.items():
            with self.subTest(raw_category=raw_category):
                category = classify_product_category(raw_category)
                self.assertIsNotNone(category)
                self.assertEqual(category.code, expected_code)

    def test_every_api_parent_category_is_classified(self) -> None:
        # PRD 2.2, 4.5 — 2026-11-17 전체 물품 확대. 2026-11-12~18 두 API 원본에서 본 상위분류 21개.
        expected = {
            "귀금속 > 반지": "jewelry",
            "기타물품 > 안경": "etc",
            "도서용품 > 소설": "books",
            "무주물 > 무주물": "ownerless",
            "산업용품 > 기타물품": "industrial",
            "서류 > 서류": "documents",
            "쇼핑백 > 쇼핑백": "shopping_bag",
            "스포츠용품 > 스포츠용품": "sports",
            "악기 > 관악기": "instrument",
            "유가증권 > 상품권": "securities",
            "유류품 > 유류품": "abandoned",
            "자동차 > 자동차열쇠": "car",
            "증명서 > 신분증": "certificate",
            "컴퓨터 > 삼성노트북": "computer",
            "현금 > 현금": "cash",
            # 예전에는 상위분류가 같아도 하위분류 때문에 버리던 물품이다.
            "전자기기 > 스마트워치": "electronics",
            "의류 > 기타의류": "clothing",
        }
        for raw_category, expected_code in expected.items():
            with self.subTest(raw_category=raw_category):
                self.assertEqual(classify_product_category(raw_category).code, expected_code)
        self.assertEqual(len(SERVICE_CATEGORIES), 21)

    def test_spacing_and_unicode_variants(self) -> None:
        variants = (
            "  지갑>남성용지갑 ",
            "지갑  >  남성용   지갑",
            "지갑 ＞ 남성용 지갑",
        )
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(classify_product_category(value).code, "wallet")

    def test_parent_must_match_exactly(self) -> None:
        # PRD 4.5 — 부분일치 금지는 상위분류 비교에도 그대로 적용한다.
        for value in ("지갑류 > 기타", "반려동물 > 강아지", "현", "", None):
            with self.subTest(value=value):
                self.assertIsNone(classify_product_category(value))
        self.assertEqual(FALLBACK_CATEGORY.code, "etc")
        self.assertEqual(FALLBACK_CATEGORY.name, "기타물품")


class FakeClient:
    def __init__(self) -> None:
        self.pages = {
            1: [
                {
                    "atcId": "A1",
                    "fdSn": "1",
                    "prdtClNm": "지갑 > 남성용 지갑",
                    "fdYmd": "2026-08-20",
                    "fdPrdtNm": "검정 지갑",
                },
                {
                    "atcId": "A2",
                    "fdSn": "1",
                    "prdtClNm": "전자기기 > 스마트워치",
                    "fdYmd": "2026-08-20",
                },
            ],
            2: [
                {
                    "atcId": "A3",
                    "fdSn": "1",
                    "prdtClNm": "카드>신용(체크)카드",
                    "fdYmd": "2026-08-19",
                },
                {
                    "atcId": "A4",
                    "fdSn": "1",
                    "prdtClNm": "반려동물 > 강아지",
                    "fdYmd": "2026-08-19",
                },
            ],
            3: [
                {
                    "atcId": "A5",
                    "fdSn": "1",
                    "prdtClNm": "  ",
                    "fdYmd": "2026-08-19",
                },
            ],
        }

    def fetch_page(self, page_no: int, num_of_rows: int = 100, **filters: str):
        return self.pages.get(page_no, []), 5


class FoundItemsExportTests(unittest.TestCase):
    def collect(self):
        return collect_selected_rows(
            FakeClient(),
            source="partner",
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 26),
            rows_per_page=2,
        )

    def test_collects_every_category(self) -> None:
        rows, summary = self.collect()

        self.assertEqual(
            sorted(row["atc_id"] for row in rows), ["A1", "A2", "A3", "A4"]
        )
        self.assertEqual(summary["selected_count"], 4)
        codes = {row["atc_id"]: row["service_category_code"] for row in rows}
        self.assertEqual(codes["A2"], "electronics")
        # 모르는 상위분류도 버리지 않고 기타물품으로 둔다. 원문 분류명은 그대로다.
        self.assertEqual(codes["A4"], "etc")
        raw = {row["atc_id"]: row["raw_category_name"] for row in rows}
        self.assertEqual(raw["A4"], "반려동물 > 강아지")

    def test_blank_category_is_counted_invalid(self) -> None:
        # DB의 found_items_raw_category_not_blank 제약을 어기는 행은 저장 전에 거른다.
        rows, summary = self.collect()

        self.assertNotIn("A5", [row["atc_id"] for row in rows])
        self.assertEqual(summary["invalid_count"], 1)

    def test_writes_utf8_bom_csv(self) -> None:
        rows, _ = self.collect()
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "items.csv"
            write_csv(rows, path)
            with path.open(encoding="utf-8-sig", newline="") as source:
                saved = list(csv.DictReader(source))

        self.assertEqual(len(saved), 4)
        by_id = {row["atc_id"]: row for row in saved}
        self.assertEqual(by_id["A1"]["service_category_code"], "wallet")
        self.assertEqual(by_id["A4"]["service_category_name"], "기타물품")


if __name__ == "__main__":
    unittest.main()
