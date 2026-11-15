"""PRD 4.5 — item embeddings are ordered, validated batches with safe failures."""
import io
import json
from unittest.mock import patch

import pytest

from retriever_lost_found.integrations.embeddings import BgeM3Client


def response(rows, **extra):
    return io.BytesIO(json.dumps({"data": rows, **extra}).encode())


def test_item_batch_preserves_input_order_and_does_not_use_query_cache():
    vector = [0.25] * 1024
    responses = [response([{"index": 1, "embedding": vector},
                           {"index": 0, "embedding": [0.5] * 1024}]),
                 response([{"index": 0, "embedding": vector}])]
    requests = []

    def open_request(request, **kwargs):
        requests.append(json.loads(request.data))
        return responses.pop(0)

    with patch("retriever_lost_found.integrations.embeddings.build_opener") as opener:
        opener.return_value.open.side_effect = open_request
        client = BgeM3Client("test-only")
        result = client.embed_many(["검정 구찌 카드지갑", "흰 에어팟"])
        client.embed_many(["검정 구찌 카드지갑"])
    assert result == [[0.5] * 1024, vector]
    assert len(requests) == 2
    assert requests[0]["input"] == ["검정 구찌 카드지갑", "흰 에어팟"]
    assert requests[0]["provider"] == {"only": ["deepinfra"], "allow_fallbacks": False}


@pytest.mark.parametrize("rows", [
    [], [{"index": 1, "embedding": [1.0] * 1024}],
    [{"index": 0, "embedding": [0.0] * 1024}],
    [{"index": 0, "embedding": [float("nan")] * 1024}],
    [{"index": 0, "embedding": [1.0] * 1023}],
    [{"index": 0, "embedding": [True] * 1024}],
])
def test_invalid_batch_never_reaches_storage(rows):
    with patch("retriever_lost_found.integrations.embeddings.build_opener") as opener:
        opener.return_value.open.return_value = response(rows)
        with pytest.raises(RuntimeError, match="임베딩"):
            BgeM3Client("test-only").embed_many(["검정 지갑"])


def test_empty_batch_does_not_call_provider():
    with patch("retriever_lost_found.integrations.embeddings.build_opener") as opener:
        assert BgeM3Client("test-only").embed_many([]) == []
    opener.assert_not_called()
