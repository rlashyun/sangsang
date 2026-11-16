-- PRD 4.5 / Q6 — one durable job per KST ingestion cycle, including zero-work runs.
-- Existing daily aggregates, item data, vectors and worker RPC signatures are preserved.
set lock_timeout = '3s';
set statement_timeout = '30s';
select pg_advisory_xact_lock(71490311);

-- Do not change job structure underneath a worker holding a live lease.
do $$ begin
 if exists(select 1 from public.embedding_runs where status='running' and lease_until>now()) then
  raise exception 'retry migration after the embedding lease finishes';
 end if;
end $$;
alter table public.embedding_runs
 add column run_slot text not null default 'legacy'
  check(run_slot in ('legacy','morning','afternoon','manual')),
 add column started_at timestamptz;
alter table public.embedding_runs drop constraint embedding_runs_business_date_input_version_key;
alter table public.embedding_runs add unique(business_date,input_version,run_slot);
comment on column public.embedding_runs.run_slot is
 'morning=09 KST, afternoon=21 KST, manual=explicit CLI, legacy=pre-migration daily aggregate';
comment on column public.embedding_runs.started_at is
 'First lease acquisition; unknown for pre-migration jobs. Retries keep the same row and timestamp.';

create function public.embedding_cycle(p_business_date date)
returns text language sql security invoker set search_path='' as $$
 select case when exists(select 1 from public.ingestion_runs
  where item_source_code in ('partner_api','police_api')
  and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text
  and metadata->>'cleanup_enabled'='false') then 'afternoon' else 'morning' end;
$$;

create function public.embedding_ingestion_ready(p_business_date date,p_run_slot text)
returns boolean language plpgsql security invoker set search_path='' as $$
declare completed_sources integer; slot text:=p_run_slot;
begin
 if exists(select 1 from public.ingestion_runs where status='running') then return false; end if;
 if slot in ('legacy','manual') then slot:=public.embedding_cycle(p_business_date); end if;
 if slot not in ('morning','afternoon') or slot is null then return false; end if;
 select count(*) into completed_sources from (
  select distinct on(item_source_code) item_source_code,status,finished_at
  from public.ingestion_runs
  where item_source_code in ('partner_api','police_api')
  and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text
  and coalesce(metadata->>'cleanup_enabled','true')=case when slot='morning' then 'true' else 'false' end
  order by item_source_code,started_at desc,id desc
 ) s where status in ('succeeded','partial','failed') and finished_at is not null;
 return completed_sources=2;
end $$;

create or replace function public.embedding_ingestion_ready(p_business_date date)
returns boolean language sql security invoker set search_path='' as $$
 select public.embedding_ingestion_ready(p_business_date,public.embedding_cycle(p_business_date));
$$;

create function public.ensure_embedding_cycle(p_business_date date,p_run_slot text,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs; upper_id bigint; has_missing boolean; empty_count bigint; today_count bigint;
begin
 perform pg_advisory_xact_lock(71490311);
 if p_run_slot not in ('legacy','morning','afternoon','manual') or p_run_slot is null then
  raise exception 'invalid embedding cycle';
 end if;
 if not p_manual and not public.embedding_ingestion_ready(p_business_date,p_run_slot) then return null; end if;
 select coalesce(max(id),0),count(*) filter(where embedding_input=''),
  count(*) filter(where (first_seen_at at time zone 'Asia/Seoul')::date=p_business_date)
 into upper_id,empty_count,today_count from public.found_items;
 select exists(select 1 from public.found_items f where f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id
   and e.input_version='color_ko_item_name:v2' and e.is_current)) into has_missing;
 -- The shared advisory lock makes SELECT + INSERT safe and avoids consuming an
 -- identity value on every duplicate callback (INSERT ON CONFLICT does consume one).
 select * into r from public.embedding_runs where business_date=p_business_date
  and input_version='color_ko_item_name:v2' and run_slot=p_run_slot for update;
 if not found then
  insert into public.embedding_runs(business_date,run_slot,max_item_id,empty_input_count,new_today_count)
  values(p_business_date,p_run_slot,upper_id,empty_count,today_count) returning * into r;
 else
  update public.embedding_runs set
   max_item_id=greatest(max_item_id,upper_id),
   status=case when status='succeeded' and has_missing then 'pending' else status end,
   after_id=case when status='succeeded' and has_missing then 0 else after_id end,
   remaining_count=case when status='succeeded' and has_missing then null else remaining_count end,
   next_attempt_at=case when p_manual and status<>'running' then now() else next_attempt_at end,
   finished_at=case when has_missing then null else finished_at end,
   new_today_count=today_count,empty_input_count=empty_count
  where id=r.id returning * into r;
 end if;
 return to_jsonb(r)-'lease_token';
end $$;

-- Old app / CLI callers continue using the same two-argument RPC.
create or replace function public.ensure_embedding_run(p_business_date date,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare slot text;
begin
 perform pg_advisory_xact_lock(71490311);
 slot:=case when p_manual then 'manual' else public.embedding_cycle(p_business_date) end;
 -- On the cutover day, keep the existing daily row as the morning result.
 -- Never fabricate a historical AM/PM split or reset its cumulative counters.
 if slot='morning' and exists(select 1 from public.embedding_runs
  where business_date=p_business_date and run_slot='legacy') then slot:='legacy'; end if;
 return public.ensure_embedding_cycle(p_business_date,slot,p_manual);
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
  -- Each job keeps its own cycle barrier and upper ID, even after midnight.
  if not p_manual and not public.embedding_ingestion_ready(r.business_date,r.run_slot) then continue; end if;
  update public.embedding_runs set status='running',lease_token=gen_random_uuid(),
   lease_until=now()+interval '6 minutes',attempts=attempts+1,last_error=null,
   started_at=coalesce(started_at,now())
  where id=r.id returning * into r;
  return to_jsonb(r);
 end loop;
 return null;
end $$;

create or replace function public.recover_embedding_runs(p_business_date date)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare source text; released integer:=0; missing integer:=0; cleanup boolean; slot text;
 cycles boolean[]:=array[true]; business_time timestamp:=now() at time zone 'Asia/Seoul';
begin
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
  slot:=case when cleanup then 'morning' else 'afternoon' end;
  -- Ensure an unobserved cycle is visible. Do not reopen a completed morning
  -- job with afternoon arrivals; the latest-cycle ensure below handles those.
  if not exists(select 1 from public.embedding_runs where business_date=p_business_date
   and (run_slot=slot or (cleanup and run_slot='legacy'))) then
   perform public.ensure_embedding_cycle(p_business_date,slot);
  end if;
 end loop;
 perform public.ensure_embedding_run(p_business_date);
 return jsonb_build_object('released_ingestion_runs',released,'missing_invocations',missing);
end $$;

-- PRD Q6 — compact, read-only monitoring surface; identity remains a stable worker key.
create view public.embedding_runs_overview with(security_invoker=true) as
 select row_number() over(order by business_date,created_at,id) as id,
  business_date,run_slot,
  case run_slot when 'morning' then business_date+time '09:00'
   when 'afternoon' then business_date+time '21:00' end as scheduled_at_kst,
  status,created_at at time zone 'Asia/Seoul' as created_at_kst,
  started_at at time zone 'Asia/Seoul' as started_at_kst,
  finished_at at time zone 'Asia/Seoul' as finished_at_kst,
  succeeded_count,reused_count,failed_count,remaining_count,attempts,last_error,
  id as run_id,input_version,empty_input_count,new_today_count
 from public.embedding_runs;
comment on view public.embedding_runs_overview is
 'Read-only monitoring. id is consecutive display numbering, run_id is the stable job key. KST timestamps. legacy rows are unsplit historical daily aggregates.';
revoke all on public.embedding_runs_overview from public,anon,authenticated;
grant select on public.embedding_runs_overview to service_role;

do $$ declare p record; begin
 for p in select oid::regprocedure as signature from pg_proc where pronamespace='public'::regnamespace
 and proname in ('embedding_cycle','embedding_ingestion_ready','ensure_embedding_cycle') loop
  execute format('revoke all on function %s from public,anon,authenticated',p.signature);
  execute format('grant execute on function %s to service_role',p.signature);
 end loop;
end $$;
notify pgrst,'reload schema';
