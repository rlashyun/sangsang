-- PRD 4.5 / 5. ISOLATED TWO-ITEM FIXTURE DB ONLY; never run on production.
-- Run as postgres against migrated fixtures. ALWAYS rolls back.
begin;
set local statement_timeout='90s';
set local role service_role;
do $test$
declare f public.found_items; payload jsonb; n bigint; snapshot text; loc bigint; r jsonb; second jsonb; old_token uuid;
begin
 assert (select count(*) from public.found_items)=2, 'isolated two-item fixture DB required';
 assert (select count(*) from public.found_item_embeddings_bge_m3)=0, 'empty fixture embeddings required';
 assert public.item_embedding_input('블랙(검정)','구찌 카드지갑')='검정 구찌 카드지갑';
 assert public.item_embedding_input('화이트(흰)','에어팟')='흰 에어팟';
 assert public.item_embedding_input(null,'  Nike  Air 2 ')='Nike Air 2';
 assert public.item_embedding_input('기타','')='기타';
 assert public.item_embedding_input('unknown','')='';

 select * into f from public.found_items where location_match_status='matched' order by id limit 1 for update;
 -- A real item is changed only inside this rollback-only transaction.
 snapshot:=f.embedding_input;
 payload:=jsonb_build_array(jsonb_build_object('item_id',f.id,'embedded_input',snapshot,
  'content_hash',encode(extensions.digest(snapshot,'sha256'),'hex'),
  'vector_source','openrouter_half_v2','embedding',to_json(array_fill(0.25::real,array[1024]))::text));
 select count(*) into n from public.upsert_bge_m3_embeddings(payload);
 assert n=1;
 assert (select embedding::text from public.found_item_embeddings_bge_m3 where item_id=f.id) is not null;
 assert (select vector from public.keyword_rerank_vectors(array[f.storage_location_id],array[f.id])) is not null;
 select count(*) into n from public.upsert_bge_m3_embeddings(payload);
 assert n=0, 'existing embedding was overwritten';

 -- Equal inputs reuse only this fresh v2 vector, without another provider call.
 update public.found_items set item_name=f.item_name,color_name=f.color_name where id<>f.id;
 r:=public.claim_embedding_run((now() at time zone 'Asia/Seoul')::date,true);
 select public.reuse_run_embeddings((r->>'id')::bigint,(r->>'lease_token')::uuid,0,512) into n;
 assert n=1, 'identical v2 input was not reused';
 assert (select reused_count from public.embedding_runs where id=(r->>'id')::bigint)=1;
 assert (select count(distinct content_hash) from public.found_item_embeddings_bge_m3)=1;
 assert (public.finish_embedding_run((r->>'id')::bigint,(r->>'lease_token')::uuid,true)->>'status')='succeeded';

 update public.found_items set item_name='test replacement Gucci 2',color_name='화이트(흰)' where id=f.id;
 assert (select embedded_input from public.found_item_embeddings_bge_m3 where item_id=f.id)=snapshot;
 assert (select embedding_input from public.found_items where id=f.id)='흰 test replacement Gucci 2';
 assert (select vector from public.keyword_rerank_vectors(array[f.storage_location_id],array[f.id])) is not null,
  'updated item lost its intentionally preserved embedding';
 select id into loc from public.storage_locations where id<>f.storage_location_id and is_active limit 1;
 update public.found_items set storage_location_id=loc where id=f.id;
 assert (select count(*) from public.keyword_rerank_vectors(array[f.storage_location_id],array[f.id]))=0;
 assert (select vector from public.keyword_rerank_vectors(array[loc],array[f.id])) is not null;
 delete from public.found_items where id=f.id;
 assert not exists(select 1 from public.found_item_embeddings_bge_m3 where item_id=f.id);
 assert not exists(select 1 from public.found_item_search_vectors where item_id=f.id);
 assert not exists(select 1 from public.found_item_search_documents where item_id=f.id);

 insert into public.ingestion_runs(item_source_code,metadata) values
 ('police_api','{"business_date":"2099-01-01"}'),('partner_api','{"business_date":"2099-01-01"}');
 update public.ingestion_runs set status='succeeded',finished_at=now()
 where metadata->>'business_date'='2099-01-01' and item_source_code='police_api';
 assert not exists(select 1 from public.embedding_runs where business_date='2099-01-01');
 update public.ingestion_runs set status='failed',finished_at=now()
 where metadata->>'business_date'='2099-01-01' and item_source_code='partner_api';
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=1;
 r:=public.claim_embedding_run('2099-01-01',false);
 assert r is not null;
 second:=public.claim_embedding_run('2099-01-01',false);
 assert second is null, 'duplicate lease acquired';
 old_token:=(r->>'lease_token')::uuid;
 update public.embedding_runs set lease_until=now()-interval '1 second' where id=(r->>'id')::bigint;
 second:=public.claim_embedding_run('2099-01-01',false);
 assert second->>'lease_token'<>r->>'lease_token';
 begin
  perform public.save_run_embeddings((r->>'id')::bigint,old_token,'[]');
  raise exception 'expired lease accepted';
 exception when raise_exception then
  if sqlerrm<>'embedding lease unavailable' then raise; end if;
 end;
 update public.embedding_runs set max_item_id=0 where id=(second->>'id')::bigint;
 r:=public.finish_embedding_run((second->>'id')::bigint,(second->>'lease_token')::uuid,true);
 assert r->>'status'='succeeded';
end $test$;
rollback;
