"""PRD 4.5 — resumable derived-search bootstrap. Does not generate embeddings."""
import json
import re
from urllib.request import Request

from retriever_lost_found.integrations.supabase import _headers, _request_json


def seed(client, *, after=0, batch_size=1000, report=print):
    total = 0
    while True:
        request = Request(client.url + "/rest/v1/rpc/seed_found_item_search",
                          data=json.dumps({"p_after": after, "p_limit": batch_size}).encode(),
                          headers=_headers(client.secret_key, content_type=True), method="POST")
        try:
            result = _request_json(request, 55, "검색 자료 초기화")
        except Exception as error:
            code = re.search(r'"code"\s*:\s*"([A-Z0-9]{5})"', str(error))
            reason = code.group(1) if code else type(error).__name__
            raise RuntimeError(f"검색 자료 초기화 실패({reason}). after={after}에서 재개하세요.") from None
        after = result["last_id"]
        total += result["read"]
        report({**result, "processed": total})
        if not result["read"]:
            return after


if __name__ == "__main__":
    import argparse
    from retriever_lost_found.config import read_env_value, read_optional_env_value
    from retriever_lost_found.integrations.supabase import SupabaseFoundItemClient
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", type=int, default=0)
    args = parser.parse_args()
    seed(SupabaseFoundItemClient(read_env_value("SUPABASE_URL"),
         read_optional_env_value("SUPABASE_SECRET_KEY") or read_env_value("SUPABASE_SERVICE_ROLE_KEY")),
         after=args.after)
