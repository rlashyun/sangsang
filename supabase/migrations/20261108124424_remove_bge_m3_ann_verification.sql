-- Explicit user request: remove only the optional ANN index and verification RPC.
-- Stored vectors, embedding table, primary key, triggers and import RPCs remain intact.
set lock_timeout = '5s';
drop function if exists public.match_bge_m3_scoped(extensions.vector,bigint[],integer,boolean);
drop index if exists public.found_item_embeddings_bge_m3_cosine_ivfflat;
notify pgrst, 'reload schema';
reset lock_timeout;
