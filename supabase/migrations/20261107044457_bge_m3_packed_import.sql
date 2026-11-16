-- PRD 5: retain service_role-only authorization; no new public Edge endpoint.
-- OpenPGP's standard compressed envelope is decoded by existing pgcrypto.
-- The per-request envelope key travels under TLS; it is not an authorization key.
create function public.upsert_bge_m3_packed(packed text,envelope_key text)
returns table(item_id bigint) language plpgsql set search_path='' as $$
declare records jsonb;
begin
 if octet_length(packed)>8000000 then raise exception 'batch too large' using errcode='22023'; end if;
 records:=extensions.pgp_sym_decrypt(decode(packed,'base64'),envelope_key)::jsonb;
 if jsonb_typeof(records)<>'array' or jsonb_array_length(records)>512 then
  raise exception 'invalid batch' using errcode='22023';
 end if;
 return query select * from public.upsert_bge_m3_embeddings(records);
end $$;
revoke all on function public.upsert_bge_m3_packed(text,text) from public,anon,authenticated;
grant execute on function public.upsert_bge_m3_packed(text,text) to service_role;
