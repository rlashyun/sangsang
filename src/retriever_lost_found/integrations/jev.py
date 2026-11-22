"""OpenRouter Decisions API(알파) — Jev 같은 판정 모델에 예/아니오(noul) 질문을 묶어 보낸다.

요청: POST /api/alpha/decisions {model, state, questions: {key: {type: "noul", instructions}}}
응답: answers[key] = {"type": "noul", "noul": 예 확률} 또는 {"type": "refusal"}.
"""

import json
from http.client import HTTPException
from typing import Any
from urllib.request import Request, build_opener

from .embeddings import NoRedirect

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class OpenRouterDecisions:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def decide(self, model: str, state: Any, questions: dict[str, dict[str, Any]], *,
               timeout: float) -> dict[str, float | None]:
        """질문 키마다 예 확률. 거절·누락·형식 오류인 질문은 None(호출자는 남긴다)."""
        request = Request(
            DECISIONS_URL,
            data=json.dumps({"model": model, "state": state, "questions": questions}, ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                answers = json.loads(response.read(1_000_001))["answers"]
        except (OSError, HTTPException, ValueError, KeyError, TypeError) as error:
            # PRD 5 — 공급자 응답 본문·키는 오류에 싣지 않는다.
            raise RuntimeError("판정 모델을 호출하지 못했습니다.") from error
        result: dict[str, float | None] = {}
        for key in questions:
            answer = answers.get(key) if isinstance(answers, dict) else None
            value = answer.get("noul") if isinstance(answer, dict) and answer.get("type") == "noul" else None
            result[key] = float(value) if isinstance(value, (int, float)) and 0 <= value <= 1 else None
        return result
