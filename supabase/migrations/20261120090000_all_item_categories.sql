-- PRD 2.2, 3.1, 4.5 — 2026-11-17 스프린트 결정: 6개 서비스 카테고리 제한을 없애고 전체 물품을 수집한다.
-- 분류 단위는 API prdtClNm("상위 > 하위")의 상위분류다. 2026-11-12~18 두 API 원본에서 본
-- 상위분류 21개 중 기존 6개(가방·의류·전자기기·지갑·휴대폰·카드)를 뺀 15개를 더한다.
-- 코드·이름은 src/retriever_lost_found/ingestion/categories.py의 SERVICE_CATEGORIES와 같아야 한다.
--
-- 수집 코드의 분류 순서: category_mappings 정확 매핑 → item_categories.name 상위분류 정확 일치 → 기타물품.
-- 기존 6개 행·id, category_mappings, found_items는 바꾸지 않는다. 다시 실행해도 결과가 같다.
--
-- 적용 순서: 이 마이그레이션 → 앱 배포 → 다음 수집 크론. 기존 앱은 6개 매핑만 읽으므로 영향이 없다.
-- 새 앱이 이 마이그레이션 없는 DB에 붙으면 '기타물품'을 찾지 못해 그 출처 수집이 실패하고 만료 삭제도 건너뛴다.
-- 되돌리기: 앱을 먼저 되돌린 뒤 supabase/rollbacks/20261120090000_all_item_categories.sql을 수동 실행한다.

insert into public.item_categories (code, name, sort_order, is_active)
values
    ('jewelry', '귀금속', 70, true),
    ('etc', '기타물품', 80, true),
    ('books', '도서용품', 90, true),
    ('ownerless', '무주물', 100, true),
    ('industrial', '산업용품', 110, true),
    ('documents', '서류', 120, true),
    ('shopping_bag', '쇼핑백', 130, true),
    ('sports', '스포츠용품', 140, true),
    ('instrument', '악기', 150, true),
    ('securities', '유가증권', 160, true),
    ('abandoned', '유류품', 170, true),
    ('car', '자동차', 180, true),
    ('certificate', '증명서', 190, true),
    ('computer', '컴퓨터', 200, true),
    ('cash', '현금', 210, true)
on conflict (code) do nothing;

comment on table public.item_categories is
    'Service categories = prdtClNm parent categories (all items since 2026-11-20). Ingestion maps exact category_mappings pairs first, then this name, then 기타물품.';
comment on table public.category_mappings is
    'Exact prdtClNm-to-category mappings for the original six categories. Unmapped pairs fall back to item_categories.name.';
