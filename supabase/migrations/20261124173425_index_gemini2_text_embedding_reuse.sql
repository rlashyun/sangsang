-- PRD 4.5 / 5: Gemini text-vector reuse probes content_hash, then checks the original input.
-- Production was repaired with CREATE INDEX CONCURRENTLY before recording this migration.
set local lock_timeout = '5s';
do $$
begin
  if to_regclass('public.found_item_embeddings_gemini2_text_hash_idx') is null then
    create index found_item_embeddings_gemini2_text_hash_idx
      on public.found_item_embeddings_gemini2 (content_hash)
      where image_url is null;
  elsif not exists (
    select 1 from pg_index
    where indexrelid = 'public.found_item_embeddings_gemini2_text_hash_idx'::regclass
      and indisvalid
  ) then
    raise exception 'Gemini text-vector reuse index is invalid';
  end if;
end $$;
