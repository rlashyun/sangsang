"""Resumable v2 backfill. Never deletes embeddings or prints configuration."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

from ..config import read_optional_env_value
from ..integrations.embeddings import BgeM3Client
from ..ingestion.service import KOREA_TIMEZONE
from .service import run_slice
from .store import EmbeddingStore


def main():
    parser = argparse.ArgumentParser(description="색상·물품명 기반 누락 임베딩 생성")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--until-complete", action="store_true")
    parser.add_argument("--capture-ids", type=Path)
    parser.add_argument("--capture-output", type=Path)
    args = parser.parse_args()

    def configured(name):
        return read_optional_env_value(name, args.env_file)

    store = EmbeddingStore(configured("SUPABASE_URL"),
        configured("SUPABASE_SECRET_KEY") or configured("SUPABASE_SERVICE_ROLE_KEY"))
    key = configured("OPENROUTER_API_KEY") or configured("openrouter_key")
    today = datetime.now(KOREA_TIMEZONE).date()
    capture_ids = set(json.loads(args.capture_ids.read_text())) if args.capture_ids else set()

    def report(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def capture(records):
        if args.capture_output:
            with args.capture_output.open("a", encoding="utf8") as f:
                for row in records:
                    if row["item_id"] in capture_ids:
                        f.write(json.dumps({"item_id": row["item_id"], "vector": json.loads(row["embedding"])}) + "\n")

    while True:
        run = store.claim(today, manual=True)
        if run is None:
            existing = store.ensure(today, manual=True)
            if existing and existing.get("status") == "succeeded":
                report(existing)
                return 0
            report({"status": "not_claimed"})
            return 2
        if not key:
            report(store.finish(run, False, "missing_api_key"))
            return 2
        result = run_slice(store, BgeM3Client(key), run, report=report, capture=capture)
        report(result)
        if result["status"] == "succeeded":
            return 0
        if not args.until_complete or result["status"] != "pending":
            return 1
        time.sleep(1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Provider URLs, bodies and credential values never reach terminal/logs.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), flush=True)
        sys.exit(1)
