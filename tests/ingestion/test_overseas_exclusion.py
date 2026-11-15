from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from retriever_lost_found.ingestion import service
from retriever_lost_found.ingestion.overseas import OVERSEAS_CUSTODY_NAMES, is_overseas_custody
from retriever_lost_found.ingestion.store import LocationReference, SupabaseIngestionStore


def test_reviewed_names_are_source_scoped_and_not_keyword_guesses():
    assert all(is_overseas_custody('partner_api', name) for name in OVERSEAS_CUSTODY_NAMES)
    assert is_overseas_custody('partner_api', ' 주 타이베이 대한민국대표부 ')
    assert not is_overseas_custody('police_api', '주타이베이대한민국대표부')
    assert not is_overseas_custody('partner_api', '주한독일대사관')
    assert not is_overseas_custody('partner_api', '주독일대사관 서울안내소')
    assert not is_overseas_custody('partner_api', None)


def test_database_and_ingestion_use_the_same_reviewed_list():
    migration = Path(__file__).resolve().parents[2] / 'supabase/migrations/20261107041612_exclude_overseas_custody.sql'
    text = migration.read_text(encoding='utf-8')
    names = set(re.findall(r"'(주[^']+)'", text))
    assert names == OVERSEAS_CUSTODY_NAMES


def test_overseas_row_preserves_raw_fields_and_skips_all_detail_lookups():
    raw = {'depPlace': '주독일대사관', 'fdPrdtNm': '원문 지갑'}
    selected = {'atc_id':'A','found_sequence':'1','raw_storage_name':'주독일대사관',
        'raw_category_name':'지갑','color_name':'검정','item_name':'원문 지갑','description':'원문 설명',
        'image_url':'','registered_on':'2026-11-06','normalized_search_text':'지갑',
        'raw_payload':json.dumps(raw,ensure_ascii=False)}
    rows = service._build_database_rows([selected],source_code='partner_api',category_ids={'지갑':1},
        aliases={'주독일대사관':1},locations=[LocationReference(1,'주독일대사관','주독일대사관')])
    assert rows[0]['location_match_status'] == 'excluded_overseas'
    assert rows[0]['storage_location_id'] is None
    assert rows[0]['raw_payload'] == raw
    assert rows[0]['raw_storage_name'] == selected['raw_storage_name']
    assert rows[0]['item_name'] == selected['item_name']
    assert rows[0]['description'] == selected['description']
    with patch.object(service,'_fetch_detail') as fetch:
        stats = service._enrich_ambiguous_rows(rows,source_code='partner_api',service_key='fake',
            mappings={},deadline=time.monotonic()+1)
    fetch.assert_not_called()
    assert stats['eligible'] == 0


def test_latest_overseas_detail_is_excluded_even_with_a_mapping():
    rows = [{'atc_id':'A','found_sequence':'1','location_match_status':'ambiguous','storage_location_id':None}]
    detail = {'detail_department':'주독일대사관','detail_org_id':'V1','detail_checked_at':'2026-11-07T00:00:00Z'}
    with patch.object(service,'_fetch_detail',return_value=detail):
        service._enrich_ambiguous_rows(rows,source_code='partner_api',service_key='fake',
            mappings={('주독일대사관','V1'):1},deadline=time.monotonic()+1)
    assert rows[0]['location_match_status'] == 'excluded_overseas'
    assert rows[0]['storage_location_id'] is None


def test_audit_counts_use_saved_db_status_and_keep_exclusion_out_of_unmatched():
    db = SupabaseIngestionStore('https://example.supabase.co','fake')
    saved = [{'id':1,'location_match_status':'excluded_overseas'},
             {'id':2,'location_match_status':'unmatched'}, {'id':3,'location_match_status':'matched'}]
    stats = {}
    with patch.object(db,'_request',return_value=saved) as request:
        assert db.upsert_found_items([{}, {}, {}],statistics=stats) == 1
    request.assert_called_once()
    assert stats == {'excluded_overseas':1}


def test_daily_sync_audits_saved_exclusions_without_extra_network_lookups():
    db = Mock(secret_key='fake')
    db.latest_registered_on.return_value = date(2026,11,6)
    db.create_run.return_value = 1
    db.load_category_ids.return_value = {'지갑':1}
    db.load_aliases.return_value = {}
    db.load_locations.return_value = []
    db.existing_identities.return_value = set()
    db.delete_expired.return_value = 0
    selected = {'atc_id':'A','found_sequence':'1','raw_storage_name':'주독일대사관',
        'raw_category_name':'지갑','color_name':'','item_name':'원문 지갑','description':'원문 설명',
        'image_url':'','registered_on':'2026-11-06','normalized_search_text':'지갑','raw_payload':'{}'}
    summary = {'fetched_count':1,'selected_count':1,'invalid_count':0,
        'outside_range_count':0,'api_total_count':1}
    def save(rows, *, statistics):
        assert rows[0]['location_match_status'] == 'excluded_overseas'
        statistics['excluded_overseas'] = 1
        return 0
    db.upsert_found_items.side_effect = save
    with patch.object(service,'collect_selected_rows',return_value=([selected],summary)), \
         patch.object(service,'create_source_client',return_value=SimpleNamespace(service_key='fake')), \
         patch.object(service,'_fetch_detail') as fetch:
        result=service._sync_source(db,source='partner',today=date(2026,11,7))
    assert result['status'] == 'succeeded'
    assert result['excluded_overseas'] == 1
    assert result['unmatched'] == 0
    audit=db.finish_run.call_args.args[1]
    assert audit['metadata']['excluded_overseas_count'] == 1
    assert audit['selected_count'] == 1
    assert audit['unmatched_count'] == 0
    db.load_detail_mappings.assert_not_called()
    fetch.assert_not_called()
