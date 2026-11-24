from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
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
        with pytest.raises(RuntimeError, match="축소본"):
            client.add_thumbnails(rows, strict=True)


def test_photo_candidates_include_only_ready_thumbnails_for_the_current_source():
    client = SupabaseFoundItemClient("https://project.supabase.co", "dummy")
    origin = "https://minwon24.police.go.kr"
    current = f"{origin}/photo.jpg"
    stale = f"{origin}/old.jpg"
    candidates = [
        {"id": 1, "storage_location_id": 7, "embedding_input": "검정 지갑", "image_url": current},
        {"id": 2, "storage_location_id": 7, "image_url": current},
        {"id": 3, "storage_location_id": 7, "image_url": current},
        {"id": 4, "storage_location_id": 7, "image_url": f"{origin}/img02_no_img.gif?cache=1"},
    ]
    current_key = thumbnail_key(1, current)
    records = [
        {"item_id": 1, "source_url": current, "object_key": current_key},
        {"item_id": 2, "source_url": stale, "object_key": thumbnail_key(2, stale)},
    ]
    with patch("retriever_lost_found.integrations.supabase._request_json", side_effect=[candidates, records]) as query:
        selected = client.fetch_photo_candidates([7], [1, 2, 3, 4])

    assert selected == [{
        **candidates[0],
        "thumbnail_url": f"https://project.supabase.co/storage/v1/object/public/found-item-thumbnails/{current_key}",
    }]
    assert query.call_count == 2
    candidate_request, thumbnail_request = (urlsplit(call.args[0].full_url) for call in query.call_args_list)
    assert candidate_request.path == "/rest/v1/found_items"
    assert parse_qs(candidate_request.query)["storage_location_id"] == ["in.(7)"]
    assert thumbnail_request.path == "/rest/v1/found_item_thumbnails"
    thumbnail_filters = parse_qs(thumbnail_request.query)
    assert thumbnail_filters["item_id"] == ["in.(1,2,3)"]
    assert thumbnail_filters["status"] == ["eq.ready"]
    assert thumbnail_filters["deleted_at"] == ["is.null"]


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


@pytest.mark.parametrize("path", ["/api/cron/thumbnail-sync", "/api/cron/thumbnail-sync-afternoon"])
def test_thumbnail_recovery_cron_requires_secret_and_runs_worker_inline(path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    with patch.dict("os.environ", {"CRON_SECRET": "test-secret"}), patch(
        "retriever_lost_found.web.cron.process_pending_thumbnails", return_value={"status": "already_running"}
    ) as process:
        assert client.get(path).status_code == 401
        assert process.call_count == 0
        response = client.get(path, headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 200
        assert response.json() == {"status": "already_running"}
        assert response.headers["cache-control"] == "no-store"
        assert process.call_count == 1
