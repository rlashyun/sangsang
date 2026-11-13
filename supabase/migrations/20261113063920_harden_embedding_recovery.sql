-- PRD 4.5 / 5 — recover lost cron deliveries and interrupted 300-second functions.
create function public.recover_embedding_runs(p_business_date date)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare source text; released integer:=0; missing integer:=0;
begin
 -- Only the daily recovery after the entire 09:00 Hobby scheduling window
 -- may declare an unobserved invocation. Never touch manual catch-up jobs.
 if p_business_date<>(now() at time zone 'Asia/Seoul')::date
 or (now() at time zone 'Asia/Seoul')::time<time '11:00' then return null; end if;
 perform pg_advisory_xact_lock(71490311);
 update public.ingestion_runs set status='failed',finished_at=now(),
  error_message='scheduled ingestion exceeded its 300-second execution limit; recovered'
 where status='running' and metadata->>'trigger'='vercel_cron_incremental'
 and started_at<now()-interval '6 minutes';
 get diagnostics released=row_count;
 foreach source in array array['partner_api','police_api'] loop
  if not exists(select 1 from public.ingestion_runs
   where item_source_code=source and coalesce(metadata->>'business_date',to_char(started_at at time zone 'Asia/Seoul','YYYY-MM-DD'))=p_business_date::text) then
   insert into public.ingestion_runs(item_source_code,status,finished_at,error_message,metadata)
   values(source,'failed',now(),'scheduled ingestion invocation not observed; recovery marker only',
    jsonb_build_object('business_date',p_business_date,'trigger','embedding_recovery_missing_source','upsert_completed',false));
   missing:=missing+1;
  end if;
 end loop;
 perform public.ensure_embedding_run(p_business_date);
 return jsonb_build_object('released_ingestion_runs',released,'missing_invocations',missing);
end $$;
revoke all on function public.recover_embedding_runs(date) from public,anon,authenticated;
grant execute on function public.recover_embedding_runs(date) to service_role;

-- On a fresh main DB this RPC is first introduced by the v2 migration.
revoke all on function public.keyword_rerank_vectors(bigint[],bigint[]) from public,anon,authenticated;
grant execute on function public.keyword_rerank_vectors(bigint[],bigint[]) to service_role;
notify pgrst,'reload schema';
