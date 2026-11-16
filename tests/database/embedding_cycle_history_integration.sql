-- PRD 4.5 / Q6. ISOLATED TWO-ITEM FIXTURE DB ONLY; never run on production.
begin;
set local role service_role;
do $test$
declare r jsonb; morning_id bigint; afternoon_id bigint; sequence_value bigint; token uuid; first_start timestamptz;
begin
 assert (select count(*) from public.found_items)=2, 'isolated two-item fixture DB required';
 assert (select count(*) from public.found_item_embeddings_bge_m3)=0, 'empty fixture embeddings required';
 -- Seed canonical vectors so both scheduled cycles have zero work.
 perform public.upsert_bge_m3_embeddings(jsonb_agg(jsonb_build_object(
  'item_id',id,'embedded_input',embedding_input,
  'content_hash',encode(extensions.digest(embedding_input,'sha256'),'hex'),
  'vector_source','openrouter_half_v2','embedding',to_json(array_fill(0.25::real,array[1024]))::text)))
 from public.found_items;
 insert into public.ingestion_runs(item_source_code,status,finished_at,metadata) values
 ('police_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":true}'),
 ('partner_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":true}');
 r:=public.ensure_embedding_run('2099-01-01'); morning_id:=(r->>'id')::bigint;
 select last_value into sequence_value from public.embedding_runs_id_seq;
 perform public.ensure_embedding_run('2099-01-01');
 perform public.ensure_embedding_run('2099-01-01');
 assert (select last_value from public.embedding_runs_id_seq)=sequence_value, 'duplicate callbacks consumed IDs';
 r:=public.claim_embedding_run('2099-01-01');
 assert r->>'started_at' is not null;
 assert public.embedding_run_batch(morning_id,(r->>'lease_token')::uuid,0)='[]'::jsonb;
 r:=public.finish_embedding_run(morning_id,(r->>'lease_token')::uuid,true);
 assert r->>'status'='succeeded' and (r->>'succeeded_count')::int=0;
 insert into public.ingestion_runs(item_source_code,status,finished_at,metadata) values
 ('police_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":false}'),
 ('partner_api','succeeded',now(),'{"business_date":"2099-01-01","cleanup_enabled":false}');
 r:=public.ensure_embedding_run('2099-01-01'); afternoon_id:=(r->>'id')::bigint;
 assert afternoon_id<>morning_id and r->>'run_slot'='afternoon';
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=2;
 r:=public.claim_embedding_run('2099-01-01');
 token:=(r->>'lease_token')::uuid; first_start:=(r->>'started_at')::timestamptz;
 assert public.claim_embedding_run('2099-01-01') is null, 'duplicate lease';
 update public.embedding_runs set lease_until=now()-interval '1 second' where id=afternoon_id;
 r:=public.claim_embedding_run('2099-01-01');
 assert (r->>'id')::bigint=afternoon_id and (r->>'lease_token')::uuid<>token;
 assert (r->>'started_at')::timestamptz=first_start, 'retry changed first start';
 r:=public.finish_embedding_run(afternoon_id,(r->>'lease_token')::uuid,true);
 assert r->>'status'='succeeded' and (r->>'succeeded_count')::int=0;
 assert (select status from public.embedding_runs where id=morning_id)='succeeded';
 assert (select array_agg(id order by id) from public.embedding_runs_overview)=array[1,2]::bigint[];
 assert (select scheduled_at_kst from public.embedding_runs_overview where run_id=morning_id)=timestamp '2099-01-01 09:00';
 assert (select scheduled_at_kst from public.embedding_runs_overview where run_id=afternoon_id)=timestamp '2099-01-01 21:00';
 assert not exists(select 1 from information_schema.columns where table_schema='public'
  and table_name='embedding_runs_overview' and column_name='lease_token');
 assert not has_table_privilege('anon','public.embedding_runs_overview','select');
 assert not has_table_privilege('authenticated','public.embedding_runs_overview','select');
 assert not has_function_privilege('anon','public.ensure_embedding_cycle(date,text,boolean)','execute');
 assert not has_function_privilege('authenticated','public.embedding_ingestion_ready(date,text)','execute');
 -- An explicit manual job is separate from both scheduled histories.
 r:=public.claim_embedding_run('2099-01-01',true);
 assert r->>'run_slot'='manual';
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01'
  and run_slot in ('morning','afternoon'))=2;
end $test$;
rollback;

-- Legacy jobs survive unchanged; no invented morning row on the cutover day.
begin;
set local role service_role;
insert into public.embedding_runs(business_date,max_item_id,status,succeeded_count)
values('2099-01-01',2,'succeeded',123);
insert into public.ingestion_runs(item_source_code,status,finished_at,metadata) values
 ('police_api','succeeded',now(),'{"business_date":"2099-01-01"}'),
 ('partner_api','succeeded',now(),'{"business_date":"2099-01-01"}');
do $$ begin
 perform public.ensure_embedding_run('2099-01-01');
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=1;
 assert (select run_slot from public.embedding_runs where business_date='2099-01-01')='legacy';
 assert (select succeeded_count from public.embedding_runs where business_date='2099-01-01')=123;
end $$;
rollback;
