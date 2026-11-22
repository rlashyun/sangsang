from __future__ import annotations

import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from retriever_lost_found.web.app import create_app

# 파비콘(시안 7-11) — frontend/public → 빌드 → web/static, 허용 목록에 있어야 서비스에서 열린다.
FAVICONS = {
    "/favicon.svg": "image/svg+xml",
    "/favicon.ico": "image/x-icon",
    "/apple-touch-icon.png": "image/png",
}


class FaviconAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(Mock(), "dummy"))

    def test_favicon_files_are_served_with_their_media_type(self) -> None:
        for path, media_type in FAVICONS.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"].split(";")[0], media_type)
                self.assertGreater(len(response.content), 0)

    def test_index_links_every_favicon_and_unlisted_files_stay_hidden(self) -> None:
        html = self.client.get("/").text
        for path in FAVICONS:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', html)
        # 허용 목록 밖의 파일은 계속 열리지 않는다.
        self.assertNotEqual(self.client.get("/favicon.png").status_code, 200)


if __name__ == "__main__":
    unittest.main()
