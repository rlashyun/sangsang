-- PRD 4.5 — 2026-11-07 user decision: keep researched Kakao queries
-- separate from the institution name used by item matching.
alter table public.storage_locations
    add column kakao_search text
    constraint storage_locations_kakao_search_check check (
        kakao_search is null or (
            kakao_search = pg_catalog.btrim(kakao_search)
            and pg_catalog.char_length(kakao_search) between 1 and 200
        )
    );

comment on column public.storage_locations.kakao_search is
    'Manually researched Kakao keyword query. NULL falls back to name in the lookup tool; not an item-matching alias or a verified place identity.';

notify pgrst, 'reload schema';
