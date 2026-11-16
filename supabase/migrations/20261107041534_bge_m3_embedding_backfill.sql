-- PRD 4.1 / 5: server-only experimental embeddings; no production search switch.
create extension if not exists vector with schema extensions;

create function public.bge_m3_input(normalized text, name text) returns text
language sql immutable parallel safe set search_path = '' as $$
 select coalesce(nullif(regexp_replace(normalized, '^\s+|\s+$', '', 'g'), ''),
                 nullif(regexp_replace(name, '^\s+|\s+$', '', 'g'), ''))
$$;
revoke all on function public.bge_m3_input(text,text) from public,anon,authenticated;
grant execute on function public.bge_m3_input(text,text) to service_role;

create table public.found_item_embeddings_bge_m3 (
 item_id bigint primary key references public.found_items(id) on delete cascade,
 embedding extensions.vector(1024) not null,
 model text not null default 'baai/bge-m3' check(model='baai/bge-m3'),
 provider text not null default 'deepinfra' check(provider='deepinfra'),
 input_version text not null default 'normalized_search_text_or_item_name:v1'
   check(input_version='normalized_search_text_or_item_name:v1'),
 content_hash text not null check(content_hash ~ '^[a-f0-9]{64}$'),
 vector_source text not null check(vector_source in ('qdrant_float16','openrouter_float32')),
 is_current boolean not null default true,
 embedded_at timestamptz not null default now(),
 check(extensions.vector_norm(embedding)>0)
);
alter table public.found_item_embeddings_bge_m3 enable row level security;
revoke all on public.found_item_embeddings_bge_m3 from public,anon,authenticated;
grant select,insert,update,delete on public.found_item_embeddings_bge_m3 to service_role;

-- Lock source rows until commit, then invalidate on subsequent text changes.
create function public.upsert_bge_m3_embeddings(records jsonb) returns table(item_id bigint)
language sql set search_path = '' as $$
 with supplied as (
  select * from jsonb_to_recordset(records) as r(item_id bigint,content_hash text,embedding text,vector_source text)
 ), current_rows as materialized (
  select f.id, s.content_hash,s.embedding,s.vector_source
  from public.found_items f join supplied s on s.item_id=f.id
  where encode(extensions.digest(public.bge_m3_input(f.normalized_search_text,f.item_name),'sha256'),'hex')=s.content_hash
  for share of f
 )
 insert into public.found_item_embeddings_bge_m3 as target(item_id,content_hash,embedding,vector_source)
 select id,content_hash,embedding::extensions.vector(1024),vector_source from current_rows
 on conflict(item_id) do update set content_hash=excluded.content_hash,embedding=excluded.embedding,
 vector_source=excluded.vector_source,is_current=true,embedded_at=now()
 where target.content_hash<>excluded.content_hash or not target.is_current
 returning target.item_id
$$;
revoke all on function public.upsert_bge_m3_embeddings(jsonb) from public,anon,authenticated;
grant execute on function public.upsert_bge_m3_embeddings(jsonb) to service_role;

create function public.invalidate_bge_m3_embedding() returns trigger
language plpgsql set search_path = '' as $$
begin
 if public.bge_m3_input(new.normalized_search_text,new.item_name) is distinct from
    public.bge_m3_input(old.normalized_search_text,old.item_name) then
  update public.found_item_embeddings_bge_m3 set is_current=false where item_id=new.id;
 end if;
 return new;
end $$;
revoke all on function public.invalidate_bge_m3_embedding() from public,anon,authenticated;
create trigger found_items_invalidate_bge_m3 after update of normalized_search_text,item_name on public.found_items
 for each row execute function public.invalidate_bge_m3_embedding();
