-- PRD 4.5 / Q6. ISOLATED TWO-ITEM FIXTURE DB ONLY; never run on production.
begin;
set local role service_role;
do $test$
declare r jsonb; pm_item bigint; morning_id bigint; saved bigint;
begin
 assert (select count(*) from public.found_items)=2, 'isolated two-item fixture DB required';
 assert (select count(*) from public.found_item_embeddings_bge_m3)=0, 'empty fixture embeddings required';
 -- Legacy morning audits remain compatible; two completed sources open the job.
 insert into public.ingestion_runs(item_source_code,status,finished_at,metadata) values
 ('police_api','succeeded',now(),'{"business_date":"2099-01-01"}'),
 ('partner_api','succeeded',now(),'{"business_date":"2099-01-01"}');
 r:=public.ensure_embedding_run('2099-01-01');
 assert r is not null;
 morning_id:=(r->>'id')::bigint;
 r:=public.claim_embedding_run('2099-01-01');
 select public.save_run_embeddings((r->>'id')::bigint,(r->>'lease_token')::uuid,
  jsonb_agg(jsonb_build_object('item_id',f.id,'embedded_input',f.embedding_input,
   'content_hash',encode(extensions.digest(f.embedding_input,'sha256'),'hex'),
   'vector_source','openrouter_half_v2','embedding',to_json(array_fill(0.25::real,array[1024]))::text)))
 into saved from public.found_items f;
 assert saved=2;
 assert (public.finish_embedding_run((r->>'id')::bigint,(r->>'lease_token')::uuid,true)->>'status')='succeeded';

 insert into public.found_items(item_source_code,atc_id,found_sequence,storage_location_id,
  location_match_status,category_id,raw_category_name,item_name,registered_on)
 values('police_api','afternoon-only','001',1,'matched',1,'지갑','오후 신규 지갑','2099-01-01') returning id into pm_item;
 -- One afternoon completion must not reuse the other source's morning completion.
 insert into public.ingestion_runs(item_source_code,metadata)
 values('police_api','{"business_date":"2099-01-01","cleanup_enabled":false}');
 update public.ingestion_runs set status='succeeded',finished_at=now()
 where status='running' and item_source_code='police_api';
 assert public.ensure_embedding_run('2099-01-01') is null, 'morning audit incorrectly satisfied afternoon barrier';
 assert public.claim_embedding_run('2099-01-01') is null, 'claim bypassed incomplete afternoon barrier';
 assert (select status from public.embedding_runs where id=morning_id)='succeeded';
 assert not exists(select 1 from public.found_item_embeddings_bge_m3 where item_id=pm_item);

 insert into public.ingestion_runs(item_source_code,metadata)
 values('partner_api','{"business_date":"2099-01-01","cleanup_enabled":false}');
 assert public.ensure_embedding_run('2099-01-01') is null, 'running afternoon source was ignored';
 update public.ingestion_runs set status='failed',finished_at=now()
 where status='running' and item_source_code='partner_api';
 assert (select status from public.embedding_runs where id=morning_id)='pending';
 assert (select count(*) from public.embedding_runs where business_date='2099-01-01')=1;
 r:=public.claim_embedding_run('2099-01-01');
 assert jsonb_array_length(public.embedding_run_batch((r->>'id')::bigint,(r->>'lease_token')::uuid,0))=1;
 assert (public.embedding_run_batch((r->>'id')::bigint,(r->>'lease_token')::uuid,0)->0->>'item_id')::bigint=pm_item;
 assert (select count(*) from public.found_item_embeddings_bge_m3)=2, 'morning vectors were changed';
 perform public.finish_embedding_run((r->>'id')::bigint,(r->>'lease_token')::uuid,true);
 update public.embedding_runs set next_attempt_at=now() where id=morning_id;
 assert public.ensure_embedding_run('2099-01-02') is null;
 r:=public.claim_embedding_run();
 assert r->>'business_date'='2099-01-01', 'midnight blocked previous-day backlog';
 assert not has_function_privilege('anon','public.embedding_ingestion_ready(date)','execute');
 assert not has_function_privilege('authenticated','public.embedding_ingestion_ready(date)','execute');
end $test$;
rollback;
