-- PRD 4.5 — 수동 백필(metadata.trigger='manual_backfill')은 끝날 때 metadata.business_date를 구간 종료일로 남긴다.
-- 2026-11-20 경찰·연계 6개월 백필에서 이 트리거가 과거 날짜(05-26~11-10)마다 'morning' 임베딩 회차 21개를 만들었고,
-- claim_embedding_run이 business_date 오름차순으로 고르므로 다음 날 정규 회차가 이 과거 회차 뒤로 밀렸다.
-- 백필 물품은 다음 정규 회차(max_item_id까지 누락 전체를 채움)나 수동 CLI가 채우므로 회차를 따로 만들지 않는다.
create or replace function public.enqueue_embeddings_on_ingestion_finish()
returns trigger language plpgsql set search_path='' as $$
begin
 if coalesce(new.metadata->>'trigger','')='manual_backfill' then return new; end if;
 if new.status in ('succeeded','partial','failed') and new.finished_at is not null
 and (old.status is distinct from new.status or old.finished_at is distinct from new.finished_at) then
  perform public.ensure_embedding_run(coalesce((new.metadata->>'business_date')::date,
   (new.started_at at time zone 'Asia/Seoul')::date));
 end if;
 return new;
end $$;

revoke all on function public.enqueue_embeddings_on_ingestion_finish() from public,anon,authenticated;
