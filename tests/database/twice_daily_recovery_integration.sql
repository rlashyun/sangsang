-- PRD 4.5 / Q6. ISOLATED TWO-ITEM FIXTURE DB ONLY; never run on production.
-- Copy the real recovery function into pg_temp with only its clock replaced.
-- This tests 11:00/23:00 boundaries regardless of the test machine's time.
begin;
do $clock$
begin
 assert (select count(*) from public.found_items)=2, 'isolated two-item fixture DB required';
 execute replace(replace(pg_get_functiondef('public.recover_embedding_runs(date)'::regprocedure),
  'public.recover_embedding_runs','pg_temp.recover_embedding_runs'),
  'now()', '(current_setting(''retriever_test.now'')::timestamptz)');
end $clock$;
set local role service_role;
set local retriever_test.now='2099-01-01 10:59:59+09';
do $$ begin
 assert pg_temp.recover_embedding_runs('2099-01-01') is null;
 assert not exists(select 1 from public.ingestion_runs);
end $$;
set local retriever_test.now='2099-01-01 11:00:00+09';
do $$ begin
 assert (pg_temp.recover_embedding_runs('2099-01-01')->>'missing_invocations')::int=2;
 assert (pg_temp.recover_embedding_runs('2099-01-01')->>'missing_invocations')::int=0;
 assert (select count(*) from public.ingestion_runs where metadata->>'cleanup_enabled'='true')=2;
 assert not exists(select 1 from public.ingestion_runs where metadata->>'cleanup_enabled'='false');
end $$;
set local retriever_test.now='2099-01-01 22:59:59+09';
do $$ begin
 assert (pg_temp.recover_embedding_runs('2099-01-01')->>'missing_invocations')::int=0;
 assert not exists(select 1 from public.ingestion_runs where metadata->>'cleanup_enabled'='false');
end $$;
set local retriever_test.now='2099-01-01 23:00:00+09';
do $$ begin
 assert (pg_temp.recover_embedding_runs('2099-01-01')->>'missing_invocations')::int=2;
 assert (pg_temp.recover_embedding_runs('2099-01-01')->>'missing_invocations')::int=0;
 assert (select count(*) from public.ingestion_runs where metadata->>'cleanup_enabled'='false')=2;
 assert pg_temp.recover_embedding_runs('2099-01-02') is null;
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=2;
 assert (select count(*) from public.found_items)=2;
 assert (select count(*) from public.found_item_embeddings_bge_m3)=0;
end $$;
rollback;

-- A 23:00 recovery must not charge afternoon arrivals to a completed morning job.
begin;
do $clock$
begin
 execute replace(replace(pg_get_functiondef('public.recover_embedding_runs(date)'::regprocedure),
  'public.recover_embedding_runs','pg_temp.recover_embedding_runs'),
  'now()', '(current_setting(''retriever_test.now'')::timestamptz)');
end $clock$;
set local role service_role;
set local retriever_test.now='2099-01-01 23:00:00+09';
do $test$
declare r jsonb; morning_id bigint;
begin
 assert (select count(*) from public.found_items)=2, 'isolated two-item fixture DB required';
 insert into public.ingestion_runs(item_source_code,status,finished_at,metadata) values
 ('police_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":true}'),
 ('partner_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":true}');
 perform public.ensure_embedding_run('2099-01-01');
 r:=public.claim_embedding_run('2099-01-01'); morning_id:=(r->>'id')::bigint;
 perform public.save_run_embeddings(morning_id,(r->>'lease_token')::uuid,
  jsonb_agg(jsonb_build_object('item_id',id,'embedded_input',embedding_input,
   'content_hash',encode(extensions.digest(embedding_input,'sha256'),'hex'),
   'vector_source','openrouter_half_v2','embedding',to_json(array_fill(0.25::real,array[1024]))::text)))
 from public.found_items;
 perform public.checkpoint_embedding_run(morning_id,(r->>'lease_token')::uuid,0,2,0);
 perform public.finish_embedding_run(morning_id,(r->>'lease_token')::uuid,true);
 insert into public.found_items(item_source_code,atc_id,found_sequence,storage_location_id,
  location_match_status,category_id,raw_category_name,item_name,registered_on)
 values('police_api','recovery-afternoon-only','001',1,'matched',1,'지갑','오후 복구 지갑','2099-01-01');
 insert into public.ingestion_runs(item_source_code,status,finished_at,metadata) values
 ('police_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":false}'),
 ('partner_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":false}');
 perform pg_temp.recover_embedding_runs('2099-01-01');
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=2;
 assert (select status from public.embedding_runs where id=morning_id)='succeeded';
 assert (select succeeded_count from public.embedding_runs where id=morning_id)=2;
 assert exists(select 1 from public.embedding_runs where business_date='2099-01-01'
  and run_slot='afternoon' and status='pending');
 r:=public.claim_embedding_run('2099-01-01');
 assert r->>'run_slot'='afternoon';
 assert jsonb_array_length(public.embedding_run_batch((r->>'id')::bigint,(r->>'lease_token')::uuid,0))=1;
end $test$;
rollback;

begin;
do $clock$
begin
 execute replace(replace(pg_get_functiondef('public.recover_embedding_runs(date)'::regprocedure),
  'public.recover_embedding_runs','pg_temp.recover_embedding_runs'),
  'now()', '(current_setting(''retriever_test.now'')::timestamptz)');
end $clock$;
set local role service_role;
set local retriever_test.now='2099-01-01 23:00:00+09';
insert into public.ingestion_runs(item_source_code,started_at,metadata) values
 ('police_api','2099-01-01 22:50:00+09',
  '{"business_date":"2099-01-01","trigger":"vercel_cron_incremental","cleanup_enabled":false}'),
 ('partner_api','2099-01-01 22:50:00+09',
  '{"business_date":"2099-01-01","trigger":"manual_catchup","cleanup_enabled":true}');
do $$ begin
 assert (pg_temp.recover_embedding_runs('2099-01-01')->>'released_ingestion_runs')::int=1;
 assert exists(select 1 from public.ingestion_runs where item_source_code='police_api'
  and metadata->>'cleanup_enabled'='false' and status='failed');
 assert exists(select 1 from public.ingestion_runs where item_source_code='partner_api'
  and metadata->>'trigger'='manual_catchup' and status='running');
end $$;
rollback;
