from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from retriever_lost_found.tools.geocode_institutions import geocode_csv, geocode_row


class FakeClient:
    def __init__(self, address_documents=None, keyword_documents=None):
        self.address_documents = address_documents or []
        self.keyword_documents = keyword_documents or []
        self.address_calls = 0
        self.keyword_calls = 0

    def geocode_address(self, address, size=1):
        self.address_calls += 1
        return {"documents": self.address_documents}

    def search_keyword(self, query, size=1):
        self.keyword_calls += 1
        return {"documents": self.keyword_documents}


class GeocodeInstitutionsTests(unittest.TestCase):
    def test_address_match_is_preferred(self):
        client = FakeClient(
            address_documents=[
                {
                    "x": "126.1",
                    "y": "37.2",
                    "road_address": {"address_name": "서울시 테스트로 1"},
                }
            ]
        )
        result = geocode_row(client, {"주소": "서울시 테스트로 1", "하위기관명": "테스트역"})
        self.assertEqual(result["좌표변환상태"], "성공")
        self.assertEqual(result["위도"], "37.2")
        self.assertEqual(result["경도"], "126.1")
        self.assertEqual(client.keyword_calls, 0)

    def test_keyword_is_used_when_address_has_no_result(self):
        client = FakeClient(
            keyword_documents=[
                {"id": "7", "x": "127.1", "y": "36.2", "address_name": "테스트 주소"}
            ]
        )
        result = geocode_row(client, {"주소": "없는 주소", "하위기관명": "테스트역"})
        self.assertEqual(result["좌표변환방식"], "키워드검색_지역일치")
        self.assertEqual(result["카카오장소ID"], "7")

    def test_keyword_result_from_another_region_is_rejected(self):
        client = FakeClient(
            keyword_documents=[
                {"id": "8", "x": "129", "y": "35", "address_name": "부산 중구 테스트로 1"}
            ]
        )
        result = geocode_row(client, {"주소": "서울 중구 없는 주소", "하위기관명": "중앙역"})
        self.assertEqual(result["좌표변환상태"], "실패")

    def test_duplicate_addresses_are_only_requested_once(self):
        client = FakeClient(address_documents=[{"x": "127", "y": "37", "address_name": "주소"}])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            target = root / "target.csv"
            source.write_text("기관명,하위기관명,연락처,주소\nA,A1,1,같은 주소\nA,A2,2,같은 주소\n", encoding="utf-8-sig")
            succeeded, failed = geocode_csv(source, target, client, checkpoint_every=0, delay=0)
        self.assertEqual((succeeded, failed), (2, 0))
        self.assertEqual(client.address_calls, 1)


if __name__ == "__main__":
    unittest.main()
