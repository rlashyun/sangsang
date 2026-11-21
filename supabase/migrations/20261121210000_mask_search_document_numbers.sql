-- PRD 3.3 · Q5 (2026-11-21 사용자 결정 ⓑ) — 검색 문서의 번호를 가린다.
--
-- 문제: 응답은 앱에서 가리지만(mask_item_row) 키워드 후보 조회·BM25·알림 매칭이 읽는
-- found_item_search_documents.search_text는 원문이다. 그래서 화면에 `1234-****-****-3456`으로 보이는
-- 카드도 `5678`로 검색하면 걸려, 가린 가운데 숫자를 검색으로 맞혀 볼 수 있다. 휴대폰 가운데 자리와
-- 주민번호(예: 생년월일 6자리)도 같다.
--
-- 결정: search_text의 번호만 Python `privacy/masking.py`의 `mask_numbers`와 같은 규칙으로 가린다.
-- 이름은 가리지 않는다 — 본인이 자기 이름으로 자기 물건을 찾을 수 있게 둔다(PRD 3.3에 기록).
--
-- 규칙 정본은 Python이다. 아래 세 정규식은 masking.py의 _REGISTRATION_RE · _MOBILE_RE · _CARD_RE 패턴
-- 문자열과 글자 하나까지 같아야 하며 tests/privacy/test_search_document_masking.py가 이를 검사한다.
-- 순서도 같다: 주민·외국인등록번호 → 휴대폰 → 카드(앞 4·뒤 4 칸만 남김).
--
-- 바꾸지 않는 것: found_items 원문(절대 규칙 3), detail(응답은 앱이 가림), content_hash(임베딩 입력 기준),
-- visible_since(갱신 시 그대로라 알림이 다시 나가지 않음), 벡터.

create or replace function public.mask_search_numbers(p_text text) returns text
language plpgsql immutable parallel safe security invoker set search_path='' as $$
declare
 v_registration constant text := '(?<![\d*xX])\d{6}[ -]?[1-8][\d*xX]{6}(?![\d*xX])';
 v_mobile constant text := '(?<![\d*xX])(01[016789])[ .-]?\d{3,4}[ .-]?(\d{4})(?![\d*xX])';
 v_card constant text := '(?<![\d*xX])(?<![\d*]-)[\d*](?:[ -]?[\d*]){12,18}(?![\d*xX])(?!-[\d*])';
 v_text text;
 v_source text;
 v_nth integer := 1;
 v_start integer;
 v_match text;
 v_masked text;
 v_cells integer;
 v_cell integer;
 v_char text;
begin
 if p_text is null or p_text !~ '\d' then
  return p_text;
 end if;
 v_text := regexp_replace(p_text, v_registration, '******-*******', 'g');
 v_text := regexp_replace(v_text, v_mobile, '\1-****-\2', 'g');
 -- 카드는 칸(숫자·별표) 수를 세어 가운데만 가린다. 길이가 바뀌지 않으므로 원래 위치에 그대로 덮어쓴다.
 -- n번째 일치를 전체 문자열에서 찾아 앞뒤 조건(lookbehind)이 잘린 부분 문자열을 보지 않게 한다.
 v_source := v_text;
 loop
  v_start := regexp_instr(v_source, v_card, 1, v_nth);
  exit when v_start = 0;
  v_match := regexp_substr(v_source, v_card, 1, v_nth);
  v_cells := length(regexp_replace(v_match, '[^\d*]', '', 'g'));
  v_masked := '';
  v_cell := 0;
  for i in 1..length(v_match) loop
   v_char := substr(v_match, i, 1);
   if v_char ~ '[\d*]' then
    v_cell := v_cell + 1;
    if v_cell > 4 and v_cell <= v_cells - 4 then
     v_char := '*';
    end if;
   end if;
   v_masked := v_masked || v_char;
  end loop;
  v_text := overlay(v_text placing v_masked from v_start for length(v_match));
  v_nth := v_nth + 1;
 end loop;
 return v_text;
end $$;
revoke all on function public.mask_search_numbers(text) from public,anon,authenticated;
grant execute on function public.mask_search_numbers(text) to service_role;

-- 20261113061646_color_item_half_embeddings_v2.sql의 본문에서 search_text 한 곳만 바꾼다.
create or replace function public.sync_search_document() returns trigger
language plpgsql security invoker set search_path='' as $$
declare h text;
begin
 h := encode(extensions.digest(public.bge_m3_input(new.normalized_search_text,new.item_name),'sha256'),'hex');
 if new.location_match_status='matched' and new.storage_location_id is not null then
  insert into public.found_item_search_documents(item_id,storage_location_id,content_hash,search_text,detail)
  values(new.id,new.storage_location_id,h,
   -- PRD 3.3 · Q5 — 번호를 가린 뒤 소문자로 만든다(x/X는 두 규칙 모두 같은 칸으로 본다).
   lower(public.mask_search_numbers(concat_ws(' ',new.item_name,new.raw_category_name,new.color_name,new.description))),
   public.search_document(new))
  on conflict(item_id) do update set storage_location_id=excluded.storage_location_id,
   content_hash=excluded.content_hash,search_text=excluded.search_text,detail=excluded.detail;
 else
  delete from public.found_item_search_documents where item_id=new.id;
 end if;
 -- Deliberately preserve an existing vector after name/color edits (user policy).
 perform public.refresh_search_vector(new.id);
 return new;
end $$;

-- 검색 문서를 처음 채우는 seed 함수도 같은 search_text를 쓴다(20261113061646 본문에서 search_text 한 곳만 바꿈).
-- 그대로 두면 이 함수로 채운 문서만 번호가 원문으로 남는다.
create or replace function public.seed_found_item_search(p_after bigint,p_limit integer default 2000)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='45s' as $$
declare f public.found_items; last_id bigint:=p_after; n integer:=0;
begin
 for f in select * from public.found_items where id>p_after order by id
  limit least(greatest(p_limit,1),5000) for share loop
  if f.location_match_status='matched' and f.storage_location_id is not null then
   insert into public.found_item_search_documents(item_id,storage_location_id,content_hash,search_text,detail)
   values(f.id,f.storage_location_id,encode(extensions.digest(public.bge_m3_input(f.normalized_search_text,f.item_name),'sha256'),'hex'),
    -- PRD 3.3 · Q5 — 트리거와 같은 규칙
    lower(public.mask_search_numbers(concat_ws(' ',f.item_name,f.raw_category_name,f.color_name,f.description))),
    public.search_document(f))
   on conflict do nothing;
  end if;
  perform public.refresh_search_vector(f.id);
  last_id:=f.id; n:=n+1;
 end loop;
 return jsonb_build_object('last_id',last_id,'read',n);
end $$;

-- 기존 검색 문서 중 결과가 달라지는 행만 고친다. 숫자가 없는 행은 함수 호출 없이 건너뛴다(구분자·별표가
-- 섞인 번호도 있어 더 좁은 사전 필터는 쓰지 않는다). found_items를 건드리지 않아 트리거·updated_at이 돌지 않는다.
-- 함수는 행마다 한 번만 계산한다(로컬 PostgreSQL 16에서 호출당 약 62µs). 수집 트리거와 잠금이 겹치면
-- 오래 기다리지 않고 실패하게 해 다음 시도로 넘긴다.
set local lock_timeout = '5s';
update public.found_item_search_documents d
set search_text = m.masked
from (
  select item_id, public.mask_search_numbers(search_text) as masked
  from public.found_item_search_documents
  where search_text ~ '\d'
) m
where d.item_id = m.item_id
  and m.masked is distinct from d.search_text;
