import unittest

from retriever_lost_found.tools.sync_police_locations import normalize_features


class EsriPoliceLocationTests(unittest.TestCase):
    def test_geojson_feature_is_normalized_for_csv(self) -> None:
        features = [
            {
                "geometry": {"type": "Point", "coordinates": [126.99, 37.56]},
                "properties": {
                    "objectid": 1,
                    "name": "서울테스트경찰서",
                    "address": "서울시 테스트로 1",
                    "sido_npa": "서울특별시경찰청",
                },
            }
        ]

        rows = normalize_features(features, default_type="경찰서")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["기관유형"], "경찰서")
        self.assertEqual(rows[0]["기관명"], "서울테스트경찰서")
        self.assertEqual(rows[0]["위도"], "37.56000000")
        self.assertEqual(rows[0]["경도"], "126.99000000")


if __name__ == "__main__":
    unittest.main()
