-- PRD 4.5 — preserve the API category path and link each item to its child category.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

create table public.item_subcategories (
    id bigint generated always as identity primary key,
    category_id smallint not null references public.item_categories(id) on delete restrict,
    name text not null check (name = btrim(name) and name <> ''),
    unique (category_id, name)
);

alter table public.item_subcategories enable row level security;
revoke all on public.item_subcategories from public, anon, authenticated;
grant select, insert on public.item_subcategories to service_role;

create function public.found_item_subcategory_name(raw_category text)
returns text language sql immutable strict set search_path = '' as $$
    select case when strpos(path, '>') > 0
                then nullif(btrim(substr(path, strpos(path, '>') + 1)), '')
           end
    from (select normalize(raw_category, NFKC) as path) as normalized
$$;

revoke all on function public.found_item_subcategory_name(text) from public, anon, authenticated;
grant execute on function public.found_item_subcategory_name(text) to service_role;

create function public.record_found_item_subcategory()
returns trigger language plpgsql set search_path = '' as $$
declare
    child_name text;
begin
    if tg_op = 'UPDATE' then
        if (new.category_id, new.raw_category_name) is not distinct from
           (old.category_id, old.raw_category_name) then
            return new;
        end if;
    end if;

    child_name := public.found_item_subcategory_name(new.raw_category_name);
    if child_name is not null and not exists (
        select 1 from public.item_subcategories as child
        where child.category_id = new.category_id and child.name = child_name
    ) then
        insert into public.item_subcategories (category_id, name)
        values (new.category_id, child_name)
        on conflict (category_id, name) do nothing;
    end if;
    return new;
end
$$;

revoke all on function public.record_found_item_subcategory() from public, anon, authenticated;
grant execute on function public.record_found_item_subcategory() to service_role;

-- Install the trigger before seeding to make the scan atomic with respect to item writes.
create trigger found_items_record_subcategory
after insert or update of category_id, raw_category_name on public.found_items
for each row execute function public.record_found_item_subcategory();

insert into public.item_subcategories (category_id, name)
select distinct item.category_id, parsed.child_name
from public.found_items as item
cross join lateral (select public.found_item_subcategory_name(item.raw_category_name) as child_name) as parsed
where parsed.child_name is not null
on conflict (category_id, name) do nothing;

-- Resolve a stable ID for each item without updating item timestamps or search projections.
create view public.found_item_category_links with (security_invoker = true) as
select item.id as item_id, item.category_id, child.id as subcategory_id, child.name as subcategory_name
from public.found_items as item
left join public.item_subcategories as child
    on child.category_id = item.category_id
    and child.name = public.found_item_subcategory_name(item.raw_category_name);

revoke all on public.found_item_category_links from public, anon, authenticated;
grant select on public.found_item_category_links to service_role;

comment on table public.item_subcategories is
    'Observed API subcategories scoped to their service parent category.';
comment on view public.found_item_category_links is
    'Each item and its service parent and optional API subcategory.';
