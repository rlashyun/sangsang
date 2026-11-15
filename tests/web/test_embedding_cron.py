"""PRD 4.5 / 5 — authenticated callbacks and daily recovery stay separate."""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from retriever_lost_found.web.cron import router


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_embedding_callback_requires_cron_authentication(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-only")
    assert client().post("/api/cron/embedding-sync").status_code == 401
    assert client().get("/api/cron/embedding-sync").status_code == 401
    assert client().get("/api/cron/embedding-sync-afternoon").status_code == 401


def test_embedding_callback_queues_then_returns_accepted(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-only")
    with patch("retriever_lost_found.web.cron.process_pending_embeddings", return_value={"status": "succeeded"}) as work:
        response = client().post("/api/cron/embedding-sync", headers={"Authorization": "Bearer test-only"})
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    work.assert_called_once()


def test_daily_embedding_recovery_reports_actual_completion(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-only")
    with patch("retriever_lost_found.web.cron.process_pending_embeddings", return_value={"status": "partial", "remaining_count": 32}):
        response = client().get("/api/cron/embedding-sync", headers={"Authorization": "Bearer test-only"})
    assert response.status_code == 500
    assert response.json()["remaining_count"] == 32
    assert response.headers["cache-control"] == "no-store"


def test_afternoon_embedding_recovery_resumes_pending_work(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-only")
    with patch("retriever_lost_found.web.cron.process_pending_embeddings", return_value={"status": "succeeded"}) as work:
        response = client().get("/api/cron/embedding-sync-afternoon", headers={"Authorization": "Bearer test-only"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    work.assert_called_once_with(recover=True)
