from unittest.mock import patch

from fastapi.testclient import TestClient

from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
from retriever_lost_found.thumbnails.storage import thumbnail_key
from retriever_lost_found.web.app import content_security_policy
from retriever_lost_found.web.cron import router
from fastapi import FastAPI


def test_only_current_source_gets_a_thumbnail_and_lookup_failure_preserves_results():
    client = SupabaseFoundItemClient("https://project.supabase.co", "secret")
    url = "https://minwon24.police.go.kr/photo"
    rows = [{"id": 1, "image_url": url}, {"id": 2, "image_url": url}]
    records = [{"item_id": 1, "source_url": url, "object_key": thumbnail_key(1, url)},
               {"item_id": 2, "source_url": "old", "object_key": thumbnail_key(2, "old")}]
    with patch("retriever_lost_found.integrations.supabase._request_json", return_value=records):
        result = client.add_thumbnails(rows)
    assert result[0]["thumbnail_url"].startswith("https://project.supabase.co/storage/v1/object/public/")
    assert "thumbnail_url" not in result[1]
    with patch("retriever_lost_found.integrations.supabase._request_json", side_effect=RuntimeError):
        assert client.add_thumbnails(rows) == rows


def test_csp_only_adds_the_exact_valid_storage_host():
    policy = content_security_policy("https://project.supabase.co")
    assert "https://project.supabase.co" in policy.split("img-src ")[1].split(";")[0]
    assert "https://project.supabase.co" not in policy.split("connect-src ")[1].split(";")[0]
    assert "evil.example" not in content_security_policy("https://evil.example")


def test_thumbnail_cron_requires_secret_and_runs_worker():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    with patch.dict("os.environ", {"CRON_SECRET": "test-secret"}), patch(
        "retriever_lost_found.web.cron.process_pending_thumbnails", return_value={"status": "completed"}
    ) as process:
        assert client.post("/api/cron/thumbnail-sync").status_code == 401
        assert process.call_count == 0
        response = client.post("/api/cron/thumbnail-sync", headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 202
        assert response.headers["cache-control"] == "no-store"
        assert process.call_count == 1
