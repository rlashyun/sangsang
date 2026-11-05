-- PRD 3.3 · 5 — 검색어가 있는 조회의 순위 계산 후보를 한 번에 돌려준다.
--
-- 배경 (2026-11-05 운영 실측, 서울역, 기존 방식 = Data API를 1,000건씩 offset으로 반복):
--   1km 8,714건  9번 왕복  6.1MB  7.5초
--   3km          첫 페이지에서 statement timeout(57014) → 502
--   5km 39,945건 40번 왕복 28.1MB 32.3초
-- offset 페이지마다 DB가 후보 전체를 다시 정렬하기 때문이다. 이 함수는 후보를 한 번만 읽는다.
--
-- 응답은 {"columns": [...], "rows": [[...], ...]}다. 행마다 키 이름을 반복하지 않아 전송량이 줄어든다.
-- 열 순서는 SupabaseFoundItemClient.SELECT_FIELDS와 같아야 하고(테스트가 확인), 클라이언트는
-- columns가 다르면 오류로 멈춘다.
--
-- 정렬하지 않고 json(jsonb 아님)으로 만든다. 운영 3km에서 jsonb + order by는 0.84~4.3초,
-- json + 정렬 없음은 0.63~0.82초였다(정렬이 work_mem을 넘어 디스크로 흘렀다). 정렬은 클라이언트가
-- 기존 Data API 조회와 같은 등록일·id 내림차순으로 맞춘다 — 퍼지 점수가 같은 물품끼리는 이 순서가
-- 그대로 남으므로(안정 정렬) 검색 결과 순서가 바뀌지 않는다.
--
-- 반경 스코프는 브라우저가 계산한 location_ids 그대로다(PRD 4.1). storage_location_id = any(...)는
-- NULL과 같지 않으므로 미매칭 물품은 나오지 않는다(PRD 6).

create or replace function public.found_items_candidates(p_location_ids bigint[])
returns json
language sql
stable
security invoker
set search_path = ''
as $$
    select json_build_object(
        'columns', json_build_array(
            'id',
            'storage_location_id',
            'item_source_code',
            'atc_id',
            'found_sequence',
            'raw_category_name',
            'color_name',
            'item_name',
            'description',
            'image_url',
            'raw_storage_name',
            'registered_on',
            'normalized_search_text'
        ),
        'rows', coalesce(
            (
                select json_agg(
                    json_build_array(
                        item.id,
                        item.storage_location_id,
                        item.item_source_code,
                        item.atc_id,
                        item.found_sequence,
                        item.raw_category_name,
                        item.color_name,
                        item.item_name,
                        item.description,
                        item.image_url,
                        item.raw_storage_name,
                        item.registered_on,
                        item.normalized_search_text
                    )
                )
                from public.found_items as item
                where item.storage_location_id = any(p_location_ids)
            ),
            '[]'::json
        )
    )
$$;

-- 절대 규칙 6 — service_role 전용.
revoke all on function public.found_items_candidates(bigint[]) from public, anon, authenticated;
grant execute on function public.found_items_candidates(bigint[]) to service_role;

comment on function public.found_items_candidates(bigint[]) is
    'Server-only search candidates for selected storage locations as {columns, rows} (unordered). PRD 3.3, 5.';
