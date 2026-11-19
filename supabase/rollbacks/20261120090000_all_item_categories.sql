-- 수동 복구 자료 — 자동 실행하거나 migrations/로 옮기지 않는다(supabase/README.md).
-- 20261120090000_all_item_categories.sql을 되돌린다. 반드시 앱(6개 필터 버전)을 먼저 되돌린 뒤 실행한다.
-- 새 앱이 남아 있으면 다음 수집에서 다시 쓰이고, 분류 행이 없어 수집이 실패한다.
--
-- 1) 새 분류 물품 삭제. 수십만 건이면 statement_timeout(57014)에 걸리므로 5,000건씩 나눠
--    "0 rows"가 나올 때까지 이 문장만 반복 실행한다. 임베딩·검색 투영·알림 발송 기록은
--    found_items(id) on delete cascade로 함께 지워진다.
delete from public.found_items
where id in (
    select item.id
    from public.found_items as item
    join public.item_categories as category on category.id = item.category_id
    where category.code in (
        'jewelry', 'etc', 'books', 'ownerless', 'industrial', 'documents', 'shopping_bag', 'sports',
        'instrument', 'securities', 'abandoned', 'car', 'certificate', 'computer', 'cash'
    )
    limit 5000
);

-- 2) 위 문장이 0건을 지운 뒤에만 실행한다. 남은 물품이 있으면 FK(on delete restrict)로 실패한다.
-- 기존 6개 분류의 하위분류 중 매핑에 없던 물품(예: 전자기기 > 스마트워치)은 기존 분류 id에 남는다.
-- 6개 필터만 쓰는 화면으로 되돌리려면 그 물품도 raw_category_name 기준으로 따로 확인해 지운다.
delete from public.item_categories
where code in (
    'jewelry', 'etc', 'books', 'ownerless', 'industrial', 'documents', 'shopping_bag', 'sports',
    'instrument', 'securities', 'abandoned', 'car', 'certificate', 'computer', 'cash'
);

comment on table public.item_categories is
    'Canonical service categories used by ingestion and search.';
comment on table public.category_mappings is
    'Exact prdtClNm-to-category mappings used by ingestion code.';
