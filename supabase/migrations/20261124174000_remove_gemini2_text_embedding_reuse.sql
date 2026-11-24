-- PRD 4.5 / 5: Generate a fresh Gemini text vector for each missing item.
-- Keep the worker's batch API and historical run counters.
set local lock_timeout = '5s';

CREATE OR REPLACE FUNCTION public.embedding_run_batch(p_run_id bigint, p_token uuid, p_after bigint, p_limit integer DEFAULT 64)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO ''
 SET statement_timeout TO '30s'
AS $function$
declare r public.embedding_runs; rows jsonb;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now();
 if not found then raise exception 'embedding lease unavailable'; end if;
 if exists(select 1 from public.ingestion_runs where status='running') then raise exception 'ingestion still running'; end if;
 if r.input_version<>'color_ko_item_name:v2' then raise exception 'unsupported embedding input version'; end if;
 with ids as materialized (
  select f.id from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_gemini2 e where e.item_id=f.id)
  order by f.id limit least(greatest(p_limit,1),64)
 )
 select coalesce(jsonb_agg(to_jsonb(s) order by s.item_id),'[]'::jsonb) into rows from (
  select f.id as item_id,f.embedding_input as embedded_input,
   encode(extensions.digest(f.embedding_input,'sha256'),'hex') as content_hash
  from ids join public.found_items f on f.id=ids.id
  where f.embedding_input<>''
 ) s;
 return rows;
end $function$;

drop function if exists public.reuse_run_embeddings(bigint, uuid, bigint, integer);
drop index if exists public.found_item_embeddings_gemini2_text_hash_idx;
