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
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=1;
 assert (select count(*) from public.found_items)=2;
 assert (select count(*) from public.found_item_embeddings_bge_m3)=0;
end $$;
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
