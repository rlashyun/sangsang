-- PRD 4.5 / 5 — 2026-11-21 김성현 검색 실험(사용자 지시): 반경 전체 의미 검색 속도를 벡터 정밀도별로 비교한다.
-- 운영 검색 경로(keyword_found_item_candidates · keyword_rerank_vectors)와 원본 벡터(found_item_search_vectors)는 바꾸지 않는다.
-- 새로 만드는 것: 1비트·2비트 양자화 사본 테이블 2개, 2비트 구간 경계 1행, 실험용 함수 4개(exp_*).
-- 사본은 적재 시점 스냅샷이다. 일일 수집으로 새로 생긴 물품은 자동으로 들어가지 않는다(트리거 없음, 실험 전용).
--
-- 1비트: pgvector binary_quantize(부호) → bit(1024). 거리 = 해밍 거리.
-- 2비트: 차원별 경계 t_i(서울역 1km 표본의 |x_i| 중앙값)로 4단계 {x≤-t, -t<x≤0, 0<x≤t, x>t} → 수준 L=2·hi+lo(0~3).
--        내적은 비트 AND의 개수로 계산한다: ΣLqLd = 4·|qh∧dh| + 2·|qh∧dl| + 2·|ql∧dh| + |ql∧dl|.
--        수준을 가운데로 옮긴 c=L-1.5의 내적 = ΣLqLd - 1.5ΣLq - 1.5ΣLd + 1024·2.25. 물품 쪽 ‖c‖로 나눠 코사인 근사로 쓴다.
-- 되돌리기: supabase/rollbacks/ 아래 같은 버전·이름의 SQL을 수동 실행한다.
set lock_timeout = '3s';

create table public.found_item_search_vectors_1bit (
  item_id bigint primary key references public.found_item_search_vectors(item_id) on delete cascade,
  storage_location_id bigint,
  content_hash text not null,
  embedding bit(1024) not null
);
create index found_item_search_vectors_1bit_location on public.found_item_search_vectors_1bit(storage_location_id, item_id);

create table public.found_item_search_vectors_2bit (
  item_id bigint primary key references public.found_item_search_vectors(item_id) on delete cascade,
  storage_location_id bigint,
  content_hash text not null,
  hi bit(1024) not null,
  lo bit(1024) not null,
  level_sum smallint not null,
  inv_norm real not null
);
create index found_item_search_vectors_2bit_location on public.found_item_search_vectors_2bit(storage_location_id, item_id);

create table public.search_vector_2bit_params (
  id smallint primary key check (id = 1),
  threshold_sq extensions.vector(1024) not null,
  source text not null
);
-- 경계: 서울역 1km 기관 17곳 물품 중 item_id % 10 = 0 표본의 차원별 |x| 중앙값(제곱해서 저장)
insert into public.search_vector_2bit_params
select 1, (array_agg((m.t*m.t)::real order by m.d))::extensions.vector(1024),
  '서울역 1km 17개 기관 item_id%10=0 표본 ' || max(m.n) || '건의 차원별 |x| 중앙값 제곱 (2026-11-21)'
from (
  select u.d, percentile_cont(0.5) within group (order by abs(u.x)) t, count(*) n
  from public.found_item_search_vectors v
  cross join lateral unnest((v.half_embedding::extensions.vector(1024))::real[]) with ordinality u(x, d)
  where v.storage_location_id = any('{3,281,282,283,293,294,2458,2550,2551,2552,2553,2798,3035,3106,4308,4817,4976}'::bigint[])
    and v.item_id % 10 = 0
  group by u.d
) m;

alter table public.found_item_search_vectors_1bit enable row level security;
alter table public.found_item_search_vectors_2bit enable row level security;
alter table public.search_vector_2bit_params enable row level security;
revoke all on public.found_item_search_vectors_1bit, public.found_item_search_vectors_2bit, public.search_vector_2bit_params
  from public, anon, authenticated;
grant select, insert, update, delete on public.found_item_search_vectors_1bit, public.found_item_search_vectors_2bit,
  public.search_vector_2bit_params to service_role;

-- 2비트 부호화: hi = (x>0), outer = (|x|>t), lo = NOT(outer XOR hi). halfvec 곱은 제곱이 너무 작아 underflow라 vector로 계산한다.
create function public.exp_quantize_2bit(v extensions.halfvec)
returns table(hi bit(1024), lo bit(1024), level_sum smallint, inv_norm real)
language sql stable security invoker set search_path = '' as $$
  with b as (
    select extensions.binary_quantize(v)::bit(1024) h,
           extensions.binary_quantize((v::extensions.vector(1024)) operator(extensions.*) (v::extensions.vector(1024))
             operator(extensions.-) p.threshold_sq)::bit(1024) o
    from public.search_vector_2bit_params p where p.id = 1
  )
  select b.h, ~(b.o # b.h),
         (2*bit_count(b.h) + bit_count(~(b.o # b.h)))::smallint,
         (1/sqrt(2.25*bit_count(b.o) + 0.25*(1024-bit_count(b.o))))::real
  from b
$$;

-- 기준선: 원본 halfvec 정확 코사인(반경 전체)
create function public.exp_semantic_half(p_location_ids bigint[], p_query extensions.halfvec, p_min float8, p_limit int)
returns table(item_id bigint, similarity float8)
language sql stable security invoker set search_path = '' set jit = 'off' set statement_timeout = '15s' as $$
  select v.item_id, 1 - (v.half_embedding operator(extensions.<=>) p_query)
  from public.found_item_search_vectors v
  where v.storage_location_id = any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
    and 1 - (v.half_embedding operator(extensions.<=>) p_query) >= p_min
  order by 2 desc, 1 desc limit p_limit
$$;

-- 1비트: 해밍 거리 상위 p_rescore건 → 원본 halfvec으로 재채점. p_rescore=0이면 양자화 점수만(similarity = 1 - 해밍/512).
create function public.exp_semantic_1bit(p_location_ids bigint[], p_query extensions.halfvec, p_min float8, p_limit int, p_rescore int)
returns table(item_id bigint, similarity float8)
language plpgsql stable security invoker set search_path = '' set jit = 'off' set statement_timeout = '15s' as $$
declare q bit(1024) := extensions.binary_quantize(p_query)::bit(1024);
begin
  if p_rescore = 0 then
    return query select b.item_id, (1 - bit_count(b.embedding # q)/512.0)::float8
      from public.found_item_search_vectors_1bit b
      where b.storage_location_id = any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
      order by bit_count(b.embedding # q), b.item_id desc limit p_limit;
  else
    return query with c as (
        select b.item_id from public.found_item_search_vectors_1bit b
        where b.storage_location_id = any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
        order by bit_count(b.embedding # q), b.item_id desc limit p_rescore)
      select v.item_id, (1 - (v.half_embedding operator(extensions.<=>) p_query))::float8
      from c join public.found_item_search_vectors v on v.item_id = c.item_id
      where 1 - (v.half_embedding operator(extensions.<=>) p_query) >= p_min
      order by 2 desc, 1 desc limit p_limit;
  end if;
end $$;

-- 2비트: 근사 코사인 상위 p_rescore건 → 원본 halfvec으로 재채점. p_rescore=0이면 근사 점수만.
create function public.exp_semantic_2bit(p_location_ids bigint[], p_query extensions.halfvec, p_min float8, p_limit int, p_rescore int)
returns table(item_id bigint, similarity float8)
language plpgsql stable security invoker set search_path = '' set jit = 'off' set statement_timeout = '15s' as $$
declare qh bit(1024); ql bit(1024); qsum int; qinv real;
begin
  select x.hi, x.lo, x.level_sum, x.inv_norm into qh, ql, qsum, qinv from public.exp_quantize_2bit(p_query) x;
  if p_rescore = 0 then
    return query select b.item_id,
        ((4*bit_count(qh & b.hi) + 2*bit_count(qh & b.lo) + 2*bit_count(ql & b.hi) + bit_count(ql & b.lo)
          - 1.5*qsum - 1.5*b.level_sum + 2304) * b.inv_norm * qinv)::float8
      from public.found_item_search_vectors_2bit b
      where b.storage_location_id = any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
      order by 2 desc, 1 desc limit p_limit;
  else
    return query with c as (
        select b.item_id from public.found_item_search_vectors_2bit b
        where b.storage_location_id = any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
        order by (4*bit_count(qh & b.hi) + 2*bit_count(qh & b.lo) + 2*bit_count(ql & b.hi) + bit_count(ql & b.lo)
          - 1.5*b.level_sum) * b.inv_norm desc, b.item_id desc limit p_rescore)
      select v.item_id, (1 - (v.half_embedding operator(extensions.<=>) p_query))::float8
      from c join public.found_item_search_vectors v on v.item_id = c.item_id
      where 1 - (v.half_embedding operator(extensions.<=>) p_query) >= p_min
      order by 2 desc, 1 desc limit p_limit;
  end if;
end $$;

revoke all on function public.exp_quantize_2bit(extensions.halfvec),
  public.exp_semantic_half(bigint[], extensions.halfvec, float8, int),
  public.exp_semantic_1bit(bigint[], extensions.halfvec, float8, int, int),
  public.exp_semantic_2bit(bigint[], extensions.halfvec, float8, int, int) from public, anon, authenticated;
grant execute on function public.exp_quantize_2bit(extensions.halfvec),
  public.exp_semantic_half(bigint[], extensions.halfvec, float8, int),
  public.exp_semantic_1bit(bigint[], extensions.halfvec, float8, int, int),
  public.exp_semantic_2bit(bigint[], extensions.halfvec, float8, int, int) to service_role;
notify pgrst, 'reload schema';
