-- PRD 5: administrative import function limits only; runtime API roles unchanged.
alter function public.upsert_bge_m3_embeddings(jsonb) set statement_timeout='45s';
alter function public.upsert_bge_m3_embeddings(jsonb) set work_mem='16MB';
alter function public.upsert_bge_m3_embeddings(jsonb) set jit=off;
alter function public.upsert_bge_m3_packed(text,text) set statement_timeout='45s';
alter function public.upsert_bge_m3_packed(text,text) set work_mem='16MB';
alter function public.upsert_bge_m3_packed(text,text) set jit=off;
notify pgrst,'reload schema';
