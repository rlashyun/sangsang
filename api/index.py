"""Vercel Python Runtime이 탐지하는 FastAPI 진입점입니다."""

from retriever_lost_found.web.app import create_runtime_app
from retriever_lost_found.web.cron import router as cron_router


app = create_runtime_app()
app.include_router(cron_router)
