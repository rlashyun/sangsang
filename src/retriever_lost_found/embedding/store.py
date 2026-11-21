"""Service-role-only embedding RPC adapter; no credentials enter reports."""
from ..ingestion.store import SupabaseIngestionStore, SupabaseRequestError


class EmbeddingStore(SupabaseIngestionStore):
    def rpc(self, name, payload):
        try:
            return self._request("POST", "rpc/" + name, payload=payload)
        except SupabaseRequestError:
            raise RuntimeError("임베딩 DB 작업 실패") from None

    def ensure(self, business_date, *, manual=False):
        return self.rpc("ensure_embedding_run", {
            "p_business_date": str(business_date), "p_manual": manual})

    def recover(self, business_date):
        return self.rpc("recover_embedding_runs", {"p_business_date": str(business_date)})

    def claim(self, business_date=None, *, manual=False):
        return self.rpc("claim_embedding_run", {
            "p_business_date": str(business_date) if business_date else None,
            "p_manual": manual})

    @staticmethod
    def lease(run):
        return {"p_run_id": run["id"], "p_token": run["lease_token"]}

    def batch(self, run, after):
        return self.rpc("embedding_run_batch", {**self.lease(run), "p_after": after})

    def categories(self, item_ids):
        """배치 물품의 원문 분류명. 임베딩 전송 마스킹이 고위험 분류를 판정하는 데 쓴다(PRD 3.3 · Q5)."""
        ids = sorted({int(item_id) for item_id in item_ids})
        if not ids:
            return {}
        try:
            rows = self._request("GET", "found_items", query=[
                ("select", "id,raw_category_name"), ("id", "in.(" + ",".join(map(str, ids)) + ")")])
        except SupabaseRequestError:
            raise RuntimeError("임베딩 DB 작업 실패") from None
        return {int(row["id"]): row.get("raw_category_name") for row in rows}

    def save(self, run, records):
        return self.rpc("save_run_embeddings", {**self.lease(run), "records": records})

    def checkpoint(self, run, after, saved, failed):
        return self.rpc("checkpoint_embedding_run", {**self.lease(run),
            "p_after": after, "p_saved": saved, "p_failed": failed})

    def finish(self, run, exhausted, error=None):
        return self.rpc("finish_embedding_run", {**self.lease(run),
            "p_exhausted": exhausted, "p_error": error})
