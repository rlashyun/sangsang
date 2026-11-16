"""Vercel Python Runtime이 탐지하는 FastAPI 진입점입니다."""

from retriever_lost_found.config import read_optional_env_value
from retriever_lost_found.web.app import create_runtime_app
from retriever_lost_found.web.cron import router as cron_router


# 대화형 검색은 기본으로 켠다. 문제가 생기면 Vercel 환경 변수 AGENT_SEARCH=0으로 기존 검색 화면으로 돌린다.
# OPENROUTER_API_KEY가 없으면 대화형 검색을 만들 수 없으므로 서버가 죽지 않게 기존 검색 화면으로 둔다.
_AGENT_SEARCH_OFF = {"0", "false", "off", "no"}
agent_search = (read_optional_env_value("AGENT_SEARCH") or "1").strip().lower() not in _AGENT_SEARCH_OFF and bool(
    read_optional_env_value("OPENROUTER_API_KEY")
)

app = create_runtime_app(agent_search=agent_search)
app.include_router(cron_router)
