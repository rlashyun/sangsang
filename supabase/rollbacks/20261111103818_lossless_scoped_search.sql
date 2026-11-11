-- Deploy the previous app before applying this rollback as a new forward migration.
-- Derived projections only. Original items, vectors and original semantic RPC are preserved.
begin;
set local lock_timeout='2s';
drop trigger if exists found_items_sync_search on public.found_items;
drop trigger if exists embeddings_sync_search on public.found_item_embeddings_bge_m3;
drop function if exists public.search_found_items_semantic_fast(bigint[],extensions.vector);
drop function if exists public.search_found_item_candidates(bigint[],text[]);
drop function if exists public.search_found_item_details(bigint[],bigint[]);
drop function if exists public.seed_found_item_search(bigint,integer);
drop function if exists public.sync_search_document();
drop function if exists public.refresh_search_vector(bigint);
drop function if exists public.sync_search_vector();
drop function if exists public.search_document(public.found_items);
drop table if exists public.found_item_search_vectors;
drop table if exists public.found_item_search_documents;
commit;
