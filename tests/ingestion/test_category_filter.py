from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from retriever_lost_found.ingestion.categories import classify_product_category
from retriever_lost_found.ingestion.collector import (
    collect_selected_rows,
    write_csv,
)


class CategoryFilterTests(unittest.TestCase):
    def test_actual_api_category_spellings(self) -> None:
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

    def test_spacing_and_unicode_variants(self) -> None:
        variants = (
            "  지갑>남성용지갑 ",
            "지갑  >  남성용   지갑",
            "지갑 ＞ 남성용 지갑",
        )
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(classify_product_category(value).code, "wallet")

    def test_unselected_category_is_rejected(self) -> None:
        self.assertIsNone(classify_product_category("전자기기 > 스마트워치"))
        self.assertIsNone(classify_product_category("의류 > 기타의류"))


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
                }
            ],
        }

    def fetch_page(self, page_no: int, num_of_rows: int = 100, **filters: str):
        return self.pages.get(page_no, []), 3


class FoundItemsExportTests(unittest.TestCase):
    def test_collects_only_supported_categories(self) -> None:
        rows, summary = collect_selected_rows(
            FakeClient(),
            source="partner",
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 26),
            rows_per_page=2,
        )

        self.assertEqual([row["atc_id"] for row in rows], ["A1", "A3"])
        self.assertEqual(summary["selected_count"], 2)

    def test_writes_utf8_bom_csv(self) -> None:
        rows, _ = collect_selected_rows(
            FakeClient(),
            source="partner",
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 26),
            rows_per_page=2,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "items.csv"
            write_csv(rows, path)
            with path.open(encoding="utf-8-sig", newline="") as source:
                saved = list(csv.DictReader(source))

        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["service_category_code"], "wallet")


if __name__ == "__main__":
    unittest.main()
