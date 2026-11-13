-- PRD 4.5 / Q6 — wait for both sources in the same morning/afternoon cycle.
-- No item/vector rewrites. Missing embeddings still reopen the same daily job.
set lock_timeout = '3s';
set statement_timeout = '30s';

create function public.embedding_ingestion_ready(p_business_date date)
returns boolean language plpgsql security invoker set search_path='' as $$
declare completed_sources integer; afternoon_started boolean;
begin
  if exists(select 1 from public.ingestion_runs where status='running') then return false; end if;
  select exists(select 1 from public.ingestion_runs
   where item_source_code in ('partner_api','police_api')
   and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text
   and metadata->>'cleanup_enabled'='false') into afternoon_started;
  select count(*) into completed_sources from (
   select distinct on(item_source_code) item_source_code,status,finished_at
   from public.ingestion_runs
   where item_source_code in ('partner_api','police_api')
   and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text
   -- Pre-change audits have no cleanup flag and belong to the morning cycle.
   and coalesce(metadata->>'cleanup_enabled','true')=case when afternoon_started then 'false' else 'true' end
   order by item_source_code,started_at desc,id desc
  ) s where status in ('succeeded','partial','failed') and finished_at is not null;
  return completed_sources=2;
end $$;
revoke all on function public.embedding_ingestion_ready(date) from public,anon,authenticated;
grant execute on function public.embedding_ingestion_ready(date) to service_role;

create or replace function public.ensure_embedding_run(p_business_date date,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs; upper_id bigint; has_missing boolean;
begin
 perform pg_advisory_xact_lock(71490311);
 if not p_manual and not public.embedding_ingestion_ready(p_business_date) then return null; end if;
 select coalesce(max(id),0) into upper_id from public.found_items;
 select exists(select 1 from public.found_items f where f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id
   and e.input_version='color_ko_item_name:v2' and e.is_current)) into has_missing;
 insert into public.embedding_runs(business_date,max_item_id,empty_input_count,new_today_count)
 select p_business_date,upper_id,count(*) filter(where embedding_input=''),
  count(*) filter(where (first_seen_at at time zone 'Asia/Seoul')::date=p_business_date)
 from public.found_items
 on conflict(business_date,input_version) do update set
  max_item_id=greatest(embedding_runs.max_item_id,excluded.max_item_id),
  status=case when embedding_runs.status='succeeded' and has_missing then 'pending' else embedding_runs.status end,
  after_id=case when embedding_runs.status='succeeded' and has_missing then 0 else embedding_runs.after_id end,
  next_attempt_at=case when p_manual and embedding_runs.status<>'running' then now() else embedding_runs.next_attempt_at end,
  finished_at=case when has_missing then null else embedding_runs.finished_at end,
  new_today_count=excluded.new_today_count,empty_input_count=excluded.empty_input_count
 returning * into r;
 return to_jsonb(r)-'lease_token';
end $$;

create or replace function public.claim_embedding_run(p_business_date date default null,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs;
begin
 perform pg_advisory_xact_lock(71490311);
 if p_manual then perform public.ensure_embedding_run(coalesce(p_business_date,(now() at time zone 'Asia/Seoul')::date),true); end if;
 if exists(select 1 from public.ingestion_runs where status='running') then return null; end if;
 if exists(select 1 from public.embedding_runs where status='running' and lease_until>now()) then return null; end if;
 for r in select * from public.embedding_runs
  where status<>'succeeded' and next_attempt_at<=now()
  and (status<>'running' or lease_until<=now())
  and (p_business_date is null or business_date=p_business_date)
  order by business_date,id for update
 loop
  -- Check each candidate's own day: midnight must not block yesterday's backlog.
  if not p_manual and not public.embedding_ingestion_ready(r.business_date) then continue; end if;
  update public.embedding_runs set status='running',lease_token=gen_random_uuid(),
   lease_until=now()+interval '6 minutes',attempts=attempts+1,last_error=null
  where id=r.id returning * into r;
  return to_jsonb(r);
 end loop;
 return null;
end $$;

create or replace function public.recover_embedding_runs(p_business_date date)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare source text; released integer:=0; missing integer:=0; cleanup boolean;
 cycles boolean[]:=array[true]; business_time timestamp:=now() at time zone 'Asia/Seoul';
begin
 -- Each Hobby window has fully elapsed before its recovery: 09 -> 11, 21 -> 23.
 if p_business_date<>business_time::date or business_time::time<time '11:00' then return null; end if;
 if business_time::time>=time '23:00' then cycles:=array[true,false]; end if;
 perform pg_advisory_xact_lock(71490311);
 with stale as materialized (
  select id from public.ingestion_runs where status='running'
  and metadata->>'trigger'='vercel_cron_incremental' and started_at<now()-interval '6 minutes'
  for update skip locked
 ) update public.ingestion_runs set status='failed',finished_at=now(),
  error_message='scheduled ingestion exceeded its 300-second execution limit; recovered'
 where id in(select id from stale);
 get diagnostics released=row_count;
 foreach cleanup in array cycles loop
  foreach source in array array['partner_api','police_api'] loop
   if not exists(select 1 from public.ingestion_runs
    where item_source_code=source
    and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text
    and coalesce(metadata->>'cleanup_enabled','true')=cleanup::text) then
    insert into public.ingestion_runs(item_source_code,status,finished_at,error_message,metadata)
    values(source,'failed',now(),'scheduled ingestion invocation not observed; recovery marker only',
     jsonb_build_object('business_date',p_business_date,'trigger','embedding_recovery_missing_source',
      'cleanup_enabled',cleanup,'upsert_completed',false));
    missing:=missing+1;
   end if;
  end loop;
 end loop;
 perform public.ensure_embedding_run(p_business_date);
 return jsonb_build_object('released_ingestion_runs',released,'missing_invocations',missing);
end $$;

-- CREATE OR REPLACE preserves the existing service-role-only grants.
notify pgrst,'reload schema';
