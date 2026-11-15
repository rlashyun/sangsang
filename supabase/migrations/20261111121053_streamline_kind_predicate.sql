-- PRD 4.5 — avoid a SQL subquery plan per document during kind checks.
create or replace function public.search_kind_matches(p_text text,p_kinds text[]) returns boolean
language plpgsql immutable parallel safe security invoker set search_path='' as $$
declare compact text; term text;
begin
 if coalesce(cardinality(p_kinds),0)=0 then return true; end if;
 compact := regexp_replace(lower(normalize(p_text,NFKC)),'[^[:alnum:]]','','g');
 foreach term in array p_kinds loop
  if term<>'' and strpos(compact,term)>0 then return true; end if;
 end loop;
 return false;
end $$;
