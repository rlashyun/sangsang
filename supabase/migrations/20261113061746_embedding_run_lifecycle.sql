-- PRD 4.5 / 5 — durable completion barrier and leased embedding slices.
create table public.embedding_runs (
 id bigint generated always as identity primary key,
 business_date date not null,
 input_version text not null default 'color_ko_item_name:v2' check(input_version='color_ko_item_name:v2'),
 status text not null default 'pending' check(status in ('pending','running','partial','failed','succeeded')),
 max_item_id bigint not null,
 after_id bigint not null default 0,
 lease_token uuid,
 lease_until timestamptz,
 next_attempt_at timestamptz not null default now(),
 succeeded_count bigint not null default 0,
 failed_count bigint not null default 0,
 remaining_count bigint,
 empty_input_count bigint,
 new_today_count bigint,
 attempts integer not null default 0,
 last_error text,
 created_at timestamptz not null default now(),
 finished_at timestamptz,
 unique(business_date,input_version)
);
alter table public.embedding_runs enable row level security;
revoke all on public.embedding_runs from public,anon,authenticated;
grant select,insert,update,delete on public.embedding_runs to service_role;
grant usage,select on sequence public.embedding_runs_id_seq to service_role;

create function public.ensure_embedding_run(p_business_date date,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs; upper_id bigint; completed_sources integer;
begin
 perform pg_advisory_xact_lock(71490311);
 if not p_manual then
  if exists(select 1 from public.ingestion_runs where status='running') then return null; end if;
  select count(*) into completed_sources from (
   select distinct on(item_source_code) item_source_code,status,finished_at
   from public.ingestion_runs
   where item_source_code in ('partner_api','police_api')
   and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text
   order by item_source_code,started_at desc,id desc
  ) s where status in ('succeeded','partial','failed') and finished_at is not null;
  if completed_sources<>2 then return null; end if;
 end if;
 select coalesce(max(id),0) into upper_id from public.found_items;
 insert into public.embedding_runs(business_date,max_item_id,empty_input_count,new_today_count)
 select p_business_date,upper_id,count(*) filter(where embedding_input=''),
  count(*) filter(where (first_seen_at at time zone 'Asia/Seoul')::date=p_business_date)
 from public.found_items
 on conflict(business_date,input_version) do update set
  max_item_id=greatest(embedding_runs.max_item_id,excluded.max_item_id),
  status=case when embedding_runs.status='succeeded' and excluded.max_item_id>embedding_runs.max_item_id then 'pending' else embedding_runs.status end,
  after_id=case when embedding_runs.status='succeeded' and excluded.max_item_id>embedding_runs.max_item_id then 0 else embedding_runs.after_id end,
  new_today_count=excluded.new_today_count,empty_input_count=excluded.empty_input_count
 returning * into r;
 return to_jsonb(r)-'lease_token';
end $$;

create function public.enqueue_embeddings_on_ingestion_finish() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
 if new.status in ('succeeded','partial','failed') and new.finished_at is not null
 and (old.status is distinct from new.status or old.finished_at is distinct from new.finished_at) then
  perform public.ensure_embedding_run(coalesce((new.metadata->>'business_date')::date,
   (new.started_at at time zone 'Asia/Seoul')::date));
 end if;
 return new;
end $$;
create trigger ingestion_runs_enqueue_embeddings after update of status,finished_at on public.ingestion_runs
 for each row execute function public.enqueue_embeddings_on_ingestion_finish();

create function public.claim_embedding_run(p_business_date date default null,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs;
begin
 perform pg_advisory_xact_lock(71490311);
 if p_manual then perform public.ensure_embedding_run(coalesce(p_business_date,(now() at time zone 'Asia/Seoul')::date),true); end if;
 if exists(select 1 from public.ingestion_runs where status='running') then return null; end if;
 if exists(select 1 from public.embedding_runs where status='running' and lease_until>now()) then return null; end if;
 select * into r from public.embedding_runs
 where status<>'succeeded' and next_attempt_at<=now()
 and (status<>'running' or lease_until<=now())
 and (p_business_date is null or business_date=p_business_date)
 order by business_date,id limit 1 for update;
 if not found then return null; end if;
 update public.embedding_runs set status='running',lease_token=gen_random_uuid(),
 lease_until=now()+interval '6 minutes',attempts=attempts+1,last_error=null
 where id=r.id returning * into r;
 return to_jsonb(r);
end $$;

create function public.embedding_run_batch(p_run_id bigint,p_token uuid,p_after bigint,p_limit integer default 64)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='20s' as $$
declare r public.embedding_runs; rows jsonb;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now();
 if not found then raise exception 'embedding lease unavailable'; end if;
 if exists(select 1 from public.ingestion_runs where status='running') then
  raise exception 'ingestion still running';
 end if;
 select coalesce(jsonb_agg(to_jsonb(s)),'[]'::jsonb) into rows from (
  select f.id as item_id,f.embedding_input as embedded_input,
   encode(extensions.digest(f.embedding_input,'sha256'),'hex') as content_hash
  from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 e
   where e.item_id=f.id and e.input_version=r.input_version and e.is_current)
  order by f.id limit least(greatest(p_limit,1),64)
 ) s;
 return rows;
end $$;

create function public.save_run_embeddings(p_run_id bigint,p_token uuid,records jsonb)
returns bigint language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare saved bigint;
begin
 perform 1 from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 if jsonb_array_length(records)>32 then raise exception 'embedding batch too large'; end if;
 select count(*) into saved from public.upsert_bge_m3_embeddings(records);
 return saved;
end $$;

create function public.checkpoint_embedding_run(p_run_id bigint,p_token uuid,p_after bigint,p_saved integer,p_failed integer)
returns boolean language plpgsql security invoker set search_path='' as $$
begin
 update public.embedding_runs set after_id=greatest(after_id,p_after),
 succeeded_count=succeeded_count+p_saved,failed_count=failed_count+p_failed,
 lease_until=now()+interval '6 minutes'
 where id=p_run_id and lease_token=p_token and status='running' and lease_until>now();
 if not found then raise exception 'embedding lease unavailable'; end if;
 return true;
end $$;

create function public.finish_embedding_run(p_run_id bigint,p_token uuid,p_exhausted boolean,p_error text default null)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare r public.embedding_runs; remaining bigint;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 select count(*) into remaining from public.found_items f where f.id<=r.max_item_id and f.embedding_input<>''
 and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id and e.input_version=r.input_version and e.is_current);
 update public.embedding_runs set remaining_count=remaining,
 status=case when remaining=0 then 'succeeded' when p_error is not null then 'failed' when p_exhausted then 'partial' else 'pending' end,
 after_id=case when p_exhausted or p_error is not null then 0 else after_id end,
 next_attempt_at=case when remaining>0 and (p_exhausted or p_error is not null) then now()+interval '15 minutes' else now() end,
 lease_token=null,lease_until=null,finished_at=case when remaining=0 then now() else null end,
 last_error=left(p_error,80)
 where id=p_run_id returning * into r;
 return to_jsonb(r)-'lease_token';
end $$;

-- Every RPC is invoker-only; no anonymous/public queue or write surface.
do $$ declare p record; begin
 for p in select oid::regprocedure as signature from pg_proc where pronamespace='public'::regnamespace
 and proname in ('ensure_embedding_run','enqueue_embeddings_on_ingestion_finish','claim_embedding_run',
 'embedding_run_batch','save_run_embeddings','checkpoint_embedding_run','finish_embedding_run') loop
  execute format('revoke all on function %s from public,anon,authenticated',p.signature);
  execute format('grant execute on function %s to service_role',p.signature);
 end loop;
end $$;
notify pgrst,'reload schema';
