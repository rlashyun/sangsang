from __future__ import annotations

import io
import urllib.error
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from retriever_lost_found.ingestion import collector,service
from retriever_lost_found.ingestion.store import SupabaseIngestionStore,SupabaseRequestError
from retriever_lost_found.integrations.deadline import DeadlineExceeded
from retriever_lost_found.integrations.partner_api import PoliceApiClient
from retriever_lost_found.integrations.police_api import PoliceFoundItemsClient


def test_daily_police_has_one_retry_owner_instead_of_30_requests():
    with patch.object(collector,'read_service_key',return_value='fake'):
        client=collector.create_source_client('police')
    with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('offline')) as network:
        with pytest.raises(RuntimeError):
            collector.fetch_page_with_retry(client,1,100,{},sleep=lambda _:None)
    assert network.call_count==5


def test_retries_and_request_timeout_share_one_collection_deadline():
    now=[0.0];timeouts=[]
    client=PoliceFoundItemsClient('fake',retry_delays=())
    client.request_deadline=150.0
    def network(*args,**kwargs):
        timeouts.append(kwargs['timeout']);now[0]+=kwargs['timeout']
        raise urllib.error.URLError('offline')
    def sleep(seconds):now[0]+=seconds
    with patch('retriever_lost_found.integrations.deadline.time.monotonic',side_effect=lambda:now[0]), \
         patch('urllib.request.urlopen',side_effect=network):
        with pytest.raises(RuntimeError):
            collector.fetch_page_with_retry(client,1,100,{},deadline=150.0,sleep=sleep)
    assert timeouts==[30,30,30,30,15]
    assert now[0]==150.0


def test_insufficient_retry_budget_stops_without_sleep_or_second_request():
    client=Mock()
    client.fetch_page.side_effect=RuntimeError('offline')
    sleep=Mock()
    with patch('retriever_lost_found.integrations.deadline.time.monotonic',return_value=9.5):
        with pytest.raises(DeadlineExceeded):
            collector.fetch_page_with_retry(client,1,100,{},deadline=10,sleep=sleep)
    client.fetch_page.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize('client',[PoliceApiClient('fake'),PoliceFoundItemsClient('fake',retry_delays=())])
def test_expired_list_budget_never_starts_a_network_request(client):
    client.request_deadline=9
    with patch('retriever_lost_found.integrations.deadline.time.monotonic',return_value=10), \
         patch('urllib.request.urlopen') as network:
        with pytest.raises(DeadlineExceeded):client.fetch_page(1)
    network.assert_not_called()


def test_partner_can_succeed_with_timeout_limited_to_remaining_budget():
    client=PoliceApiClient('fake');client.request_deadline=15
    xml=b'<response><header><resultCode>00</resultCode></header><body><items/><totalCount>0</totalCount></body></response>'
    with patch('retriever_lost_found.integrations.deadline.time.monotonic',return_value=10), \
         patch('urllib.request.urlopen',return_value=io.BytesIO(xml)) as network:
        assert client.fetch_page(1)==([],0)
    assert network.call_args.kwargs['timeout']==5


def test_expired_collection_preserves_data_and_reserves_time_to_finish_audit():
    store=Mock(secret_key='fake');store.latest_registered_on.return_value=date(2026,11,6)
    store.create_run.return_value=1;store.load_category_ids.return_value={'지갑':1}
    store.load_aliases.return_value={};store.load_locations.return_value=[]
    with patch.object(service,'create_source_client',return_value=SimpleNamespace(service_key='fake-api')), \
         patch.object(service,'collect_selected_rows',side_effect=DeadlineExceeded('budget')) as collect, \
         patch.object(service.time,'monotonic',return_value=1000):
        result=service._sync_source(store,source='police',today=date(2026,11,7),incremental_only=True)
    assert result['status']=='failed'
    assert collect.call_args.kwargs['deadline']==1150
    assert store.deadline==1295
    store.upsert_found_items.assert_not_called()
    store.delete_expired.assert_not_called()
    store.finish_run.assert_called_once()


def test_retention_cleanup_yields_before_budget_is_exhausted():
    store=SupabaseIngestionStore('https://example.supabase.co','fake')
    store.deadline=270
    with patch('retriever_lost_found.ingestion.store.time.monotonic',return_value=250), \
         patch.object(store,'_request') as request:
        assert store.delete_expired('police_api')==0
    request.assert_not_called()
    assert store.cleanup_deferred is True
    with patch('retriever_lost_found.ingestion.store.time.monotonic',return_value=200), \
         patch.object(store,'_request',return_value=[{'deleted_count':2}]) as request:
        assert store.delete_expired('police_api')==2
    request.assert_called_once()
    assert store.cleanup_deferred is False


def test_expired_db_budget_never_starts_a_request():
    store=SupabaseIngestionStore('https://example.supabase.co','fake');store.deadline=10
    with patch('retriever_lost_found.integrations.deadline.time.monotonic',return_value=11), \
         patch('urllib.request.urlopen') as network:
        with pytest.raises(SupabaseRequestError):store._request('GET','found_items')
    network.assert_not_called()
