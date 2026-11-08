from __future__ import annotations

import io
import json
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
from unittest.mock import Mock
from datetime import date
from types import SimpleNamespace

from retriever_lost_found.ingestion import service
from retriever_lost_found.ingestion.store import LocationReference, SupabaseIngestionStore


def row(identity="A1", status="ambiguous"):
    return {"atc_id": identity, "found_sequence": "1", "raw_storage_name": "역전파출소",
            "location_match_status": status, "storage_location_id": None}


def detail(status="보관중", place="밀양경찰서", code="O0002287"):
    return {"detail_department": place, "detail_org_id": code, "detail_status": status,
            "detail_checked_at": "2026-11-07T00:00:00+00:00"}


class CurrentDetailMappingTests(unittest.TestCase):
    def test_enriched_and_plain_items_can_share_one_postgrest_bulk_request(self):
        selected=[]
        for identity,name in [('A1','중앙지구대'),('A2','서울중앙지구대')]:
            selected.append({'atc_id':identity,'found_sequence':'1','raw_storage_name':name,
                'raw_category_name':'지갑','color_name':'','item_name':'원문','description':'설명',
                'image_url':'','registered_on':'2026-11-06','normalized_search_text':'지갑','raw_payload':'{}'})
        rows=service._build_database_rows(selected,source_code='police_api',category_ids={'지갑':1},
            aliases={},locations=[LocationReference(1,'서울중앙지구대','서울중앙지구대'),
                                  LocationReference(2,'충주중앙지구대','충주중앙지구대')])
        evidence={**detail(),'detail_org_name':'밀양경찰서','detail_phone':'055-000-0000'}
        with patch.object(service,'_fetch_detail',return_value=evidence):
            self.enrich(rows,{('밀양경찰서','O0002287'):245})
        store=SupabaseIngestionStore('https://example.supabase.co','fake')
        def post(*args,**kwargs):
            payload=kwargs['payload']
            self.assertEqual(set(payload[0]),set(payload[1]),'PostgREST rejects mixed object keys')
            return [{'id':i,'location_match_status':r['location_match_status']} for i,r in enumerate(payload)]
        with patch.object(store,'_request',side_effect=post):
            self.assertEqual(store.upsert_found_items(rows),0)
        self.assertEqual(rows[0]['detail_status'],'보관중')
        self.assertIsNone(rows[1]['detail_status'])

    def enrich(self, rows, mappings, **kwargs):
        return service._enrich_ambiguous_rows(
            rows, source_code="police_api", service_key="fake-key", mappings=mappings,
            deadline=kwargs.get("deadline", time.monotonic() + 2),
        )

    def test_all_treatment_states_use_same_current_place_rule(self):
        for state in ["보관중", "종결", "삭제", "반입중", ""]:
            with self.subTest(state=state), patch.object(service, "_fetch_detail", return_value=detail(state)):
                rows = [row()]
                stats = self.enrich(rows, {("밀양경찰서", "O0002287"): 245})
                self.assertEqual(rows[0]["storage_location_id"], 245)
                self.assertEqual(rows[0]["location_match_status"], "matched")
                self.assertEqual(rows[0]["raw_storage_name"], "역전파출소")
                self.assertEqual(stats["succeeded"], 1)

    def test_old_place_and_current_code_are_never_combined(self):
        rows = [row()]
        with patch.object(service, "_fetch_detail", return_value=detail()):
            self.enrich(rows, {("역전파출소", "O0002287"): 1856})
        self.assertEqual(rows[0]["location_match_status"], "unmatched")
        self.assertIsNone(rows[0]["storage_location_id"])

    def test_same_code_different_current_place_remains_distinct(self):
        rows = [row()]
        with patch.object(service, "_fetch_detail", return_value=detail(place="일동파출소", code="O0000828")):
            self.enrich(rows, {("안산상록경찰서", "O0000828"): 100, ("일동파출소", "O0000828"): 932})
        self.assertEqual(rows[0]["storage_location_id"], 932)

    def test_unique_and_unknown_names_do_not_fetch_details(self):
        rows = [row(status="matched"), row("A2", "unmatched")]
        with patch.object(service, "_fetch_detail") as fetch:
            self.enrich(rows, {})
        fetch.assert_not_called()
        self.assertEqual([r["location_match_status"] for r in rows], ["matched", "unmatched"])

    def test_duplicate_identity_is_fetched_once_per_batch(self):
        rows = [row(), row()]
        with patch.object(service, "_fetch_detail", return_value=detail()) as fetch:
            self.enrich(rows, {("밀양경찰서", "O0002287"): 245})
        fetch.assert_called_once()
        self.assertTrue(all(r["storage_location_id"] == 245 for r in rows))

    def test_transient_failure_retries_once_without_losing_current_match(self):
        rows=[row()]
        with patch.object(service,'_fetch_detail',side_effect=[TimeoutError(),detail()]) as fetch:
            stats=self.enrich(rows,{('밀양경찰서','O0002287'):245},deadline=time.monotonic()+4)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(stats['requested'],2)
        self.assertEqual(stats['retried'],1)
        self.assertEqual(stats['failed'],0)
        self.assertEqual(stats['succeeded'],1)
        self.assertEqual(rows[0]['storage_location_id'],245)

    def test_today_sized_batch_has_room_for_119_transient_retries(self):
        rows = [row(str(i)) for i in range(254)]
        seen = set()
        def fetch(source, key, identity, sequence):
            if int(identity) < 119 and identity not in seen:
                seen.add(identity)
                raise service.DetailLookupError("temporary", retryable=True, reason="timeout")
            return detail()
        with patch.object(service, "_fetch_detail", side_effect=fetch):
            stats = self.enrich(rows, {("밀양경찰서", "O0002287"): 245}, deadline=time.monotonic()+10)
        self.assertEqual(stats["requested"], 373)
        self.assertEqual(stats["retried"], 119)
        self.assertEqual(stats["succeeded"], 254)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["deferred"], 0)
        self.assertEqual(stats["failure_attempts_by_reason"], {"timeout": 119})
        self.assertTrue(all(r["storage_location_id"] == 245 for r in rows))

    def test_more_than_400_identities_still_obey_request_cap(self):
        rows = [row(str(i)) for i in range(401)]
        with patch.object(service, "_fetch_detail", return_value=detail()):
            stats = self.enrich(rows, {("밀양경찰서", "O0002287"): 245}, deadline=time.monotonic()+10)
        self.assertEqual(stats["requested"], 400)
        self.assertEqual(stats["succeeded"], 400)
        self.assertEqual(stats["deferred"], 1)

    def test_safe_failure_classification_distinguishes_network_api_and_identity(self):
        cases = [
            (urllib.error.HTTPError("https://secret", 429, "secret", {}, None), "http_429", True),
            (urllib.error.URLError(TimeoutError("secret")), "timeout", True),
            (b"not XML secret", "invalid_xml", False),
            (b"<response><header><resultCode>22</resultCode></header></response>", "api_22", False),
            (b"<response><header><resultCode>00</resultCode></header><body/></response>", "missing_item", False),
        ]
        for response, reason, retryable in cases:
            with self.subTest(reason=reason):
                kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": io.BytesIO(response)}
                with patch("urllib.request.urlopen", **kwargs):
                    with self.assertRaises(service.DetailLookupError) as caught:
                        service._fetch_detail("police_api", "secret", "A1", "1")
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(caught.exception.retryable, retryable)
                self.assertNotIn("secret", str(caught.exception))

    def test_permanent_detail_failure_does_not_retry(self):
        with patch.object(service,'_fetch_detail',side_effect=service.DetailLookupError('invalid identity')) as fetch:
            stats=self.enrich([row()],{},deadline=time.monotonic()+4)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(stats['failed'],1)

    def test_repeated_network_failure_stops_after_two_attempts(self):
        with patch.object(service,'_fetch_detail',side_effect=TimeoutError()) as fetch:
            stats=self.enrich([row()],{},deadline=time.monotonic()+4)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(stats['failed'],1)
        self.assertEqual(stats['deferred'],0)

    def test_failure_and_expired_budget_leave_no_stale_match(self):
        with patch.object(service, "_fetch_detail", side_effect=TimeoutError()):
            rows = [row()]
            stats = self.enrich(rows, {})
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(rows[0]["location_match_status"], "unmatched")
        with patch.object(service, "_fetch_detail") as fetch:
            rows = [row()]
            stats = self.enrich(rows, {}, deadline=time.monotonic() - 1)
        fetch.assert_not_called()
        self.assertEqual(stats["deferred"], 1)
        self.assertIsNone(rows[0]["storage_location_id"])

    def test_request_cap_and_running_fetch_do_not_hold_up_storage(self):
        release = threading.Event()
        def fetch(*args):
            release.wait(2)
            return detail()
        rows = [row(str(i)) for i in range(3)]
        try:
            with patch.object(service, "DETAIL_MAX_REQUESTS", 1), patch.object(service, "_fetch_detail", side_effect=fetch) as lookup:
                stats = self.enrich(rows, {}, deadline=time.monotonic() + 0.03)
                self.assertEqual(stats["requested"], 1)
                self.assertEqual(stats["deferred"], 3)
                self.assertEqual(lookup.call_count, 1)
                self.assertTrue(all(r["location_match_status"] == "unmatched" for r in rows))
        finally:
            release.set()

    def test_mapping_load_filters_source_and_ignores_inactive_targets(self):
        store = SupabaseIngestionStore("https://example.supabase.co", "fake-key")
        with patch.object(store, "_fetch_all", return_value=[
            {"normalized_raw_storage_name": "밀양경찰서", "detail_org_id": "O0002287", "storage_location_id": 245},
            {"normalized_raw_storage_name": "폐쇄기관", "detail_org_id": "X", "storage_location_id": 999},
        ]) as request:
            mappings = store.load_detail_mappings("police_api", active_ids={245})
        self.assertEqual(mappings, {("밀양경찰서", "O0002287"): 245})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["filters"], [("item_source_code", "eq.police_api")])

    def test_detail_response_identity_and_secret_redaction(self):
        xml = b'<response><header><resultCode>00</resultCode></header><body><item><atcId>A1</atcId><fdSn>1</fdSn><orgId>O1</orgId><depPlace>station</depPlace></item></body></response>'
        with patch("urllib.request.urlopen", return_value=io.BytesIO(xml)):
            self.assertEqual(service._fetch_detail("police_api", "fake-key", "A1", "1")["detail_org_id"], "O1")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(xml)):
            with self.assertRaises(RuntimeError):
                service._fetch_detail("police_api", "fake-key", "WRONG", "1")
        with patch("urllib.request.urlopen", side_effect=RuntimeError("https://example?serviceKey=fake-key")):
            with self.assertRaises(RuntimeError) as error:
                service._fetch_detail("police_api", "fake-key", "A1", "1")
        self.assertNotIn("fake-key", str(error.exception))
        self.assertNotIn("https://", str(error.exception))

    def test_sync_loads_rules_once_and_enriches_only_ambiguous_items(self):
        store = Mock(secret_key="fake-secret")
        store.latest_registered_on.return_value = date(2026, 11, 5)
        store.create_run.return_value = 1
        store.load_category_ids.return_value = {"지갑": 1}
        store.load_aliases.return_value = {}
        store.load_locations.return_value = [
            LocationReference(1, "서울중앙지구대", "서울중앙지구대"),
            LocationReference(2, "충주중앙지구대", "충주중앙지구대"),
            LocationReference(245, "밀양경찰서", "밀양경찰서"),
        ]
        store.load_detail_mappings.return_value = {("밀양경찰서", "O0002287"): 245}
        store.existing_identities.return_value = set()
        store.upsert_found_items.side_effect = lambda rows, **kwargs: sum(r["location_match_status"] in {"unmatched", "ambiguous"} for r in rows)
        store.delete_expired.return_value = 0
        selected = []
        for i, name in enumerate(["중앙지구대", "서울중앙지구대", "미등록기관"]):
            selected.append({"atc_id": str(i), "found_sequence": "1", "raw_storage_name": name,
                "raw_category_name": "지갑", "raw_payload": json.dumps({"depPlace": name}),
                "color_name": "", "item_name": "지갑", "description": "원문", "image_url": "",
                "registered_on": "2026-11-05", "normalized_search_text": "지갑"})
        summary = {"fetched_count": 3, "selected_count": 3, "invalid_count": 0,
                   "outside_range_count": 0, "api_total_count": 3}
        with patch.object(service, "create_source_client", return_value=SimpleNamespace(service_key="fake-key")), \
             patch.object(service, "collect_selected_rows", return_value=(selected, summary)), \
             patch.object(service, "_fetch_detail", return_value=detail("종결")) as fetch:
            result = service._sync_source(store, source="police", today=date(2026, 11, 7))
        self.assertEqual(result["status"], "succeeded")
        store.load_detail_mappings.assert_called_once_with("police_api", active_ids={1, 2, 245})
        fetch.assert_called_once_with("police_api", "fake-key", "0", "1")
        written = store.upsert_found_items.call_args.args[0]
        self.assertEqual([r["storage_location_id"] for r in written], [245, 1, None])
        self.assertEqual(written[0]["detail_status"], "종결")
        self.assertEqual(result["unmatched"], 1)
        self.assertEqual(store.finish_run.call_args.args[1]["metadata"]["detail_lookup"]["requested"], 1)
