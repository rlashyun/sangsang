"""Server-side model transport for conversational search."""

import json
from http.client import HTTPException
from typing import Any
from urllib.request import Request, build_opener

from .embeddings import NoRedirect


class OpenRouterAgentModel:
    def __init__(self, api_key: str, model: str = "openai/gpt-6-luna", *, timeout: float = 5, max_tokens: int = 400):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def __call__(self, instructions: str, context: dict[str, Any], schema: dict[str, Any]) -> Any:
        request = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "search_agent", "strict": True, "schema": schema},
                },
                "provider": {"require_parameters": True, "allow_fallbacks": False},
                "reasoning": {"effort": "none"},
                "max_tokens": self.max_tokens,
            }).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(65537))
            choice = payload["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("refusal"):
                raise ValueError
            return json.loads(choice["message"]["content"])
        except (OSError, HTTPException, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("대화 응답을 생성하지 못했습니다. 다시 시도해 주세요.") from None
