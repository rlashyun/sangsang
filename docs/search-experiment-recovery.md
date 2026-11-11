# 김성현 검색 실험 변경·복구 기록

기준일: 2026-11-11 KST. 임베딩 보충 이후 작업은 김성현의 검색 실험이다. 이 문서는 실험을 중단하거나 재현할 때 사용하는 유지 문서이며, 비밀값은 기록하지 않는다. 아래 상태는 당시 작업 결과를 기록한 것이며 현재 DB·배포를 다시 검사한 결과가 아니다.

## 기준점과 변경 목록

| 구분 | 기록 |
| --- | --- |
| 체크아웃 / 브랜치 | `seonghyun_search` 폴더 / `seonghyun_search` |
| 검색 코드 변경 전 | `5712b61` — 코드 기준점이며 임베딩 적재 직후의 DB 스냅샷은 아님 |
| 실험 코드 | `ebae39304281f2632765931986b5acb290219bb2` — BM25 + BGE-M3 병렬 검색 |
| 대상 DB | Supabase `retriever`, project ref `bjbqcntoqpubybrjhrge` |
| 데이터 보충 | 기존 `found_item_embeddings_bge_m3`에 누락 4,834건 삽입, 만료 3건 갱신, 합계 4,837건. 물품 원문 유지 |
| 보충 직후 기준 | `found_items` 295,205건, 임베딩 누락·만료·입력 해시 불일치 각각 0건 |
| DB 마이그레이션 | `supabase/migrations/20261111042228_scoped_bge_m3_semantic_search.sql` — 운영 적용 완료. 당시 적용 이름 `scoped_bge_m3_semantic_search` |

데이터 보충과 스키마 변경은 구분한다. 마이그레이션 SQL은 벡터 값 자체를 포함하지 않으며, 실행만으로 4,837건 또는 전체 벡터를 다시 생성하지 않는다. 보충 조건·모델·비용·검증 결과는 [실측 기록](measurements.md)의 2026-11-11 절에 있다. 일일 수집 후 자동 임베딩 갱신은 구현하지 않았다.

## DB 변경의 범위

- `vector`·`pgcrypto` 확장을 `IF NOT EXISTS`로 준비하고, 기존 BGE-M3 테이블 형식을 `CREATE TABLE IF NOT EXISTS`로 재현한다. 테이블은 이번 실험 이전부터 존재했다.
- 테이블 RLS와 service_role 전용 권한을 명시한다.
- `bge_m3_input(text,text)`와 `upsert_bge_m3_embeddings(jsonb)`를 `CREATE OR REPLACE`로 정의한다. 입력 해시가 현재 물품과 일치할 때만 벡터를 저장한다.
- `search_found_items_semantic(bigint[],vector)`를 정의한다. 기관 범위 1~300개, matched 물품, 최신 입력 해시, cosine 0.55 이상, 최대 200건을 사용한다. 함수 호출 권한은 service_role 전용이다.
- 이 파일은 이미 적용한 이력이다. 이후 수정은 새 forward migration으로 남기고 기존 파일을 고쳐 과거 적용 내용을 바꾸지 않는다.

## 코드와 Preview 설정

검색 구현은 `search/hybrid.py`, `integrations/embeddings.py`, `integrations/supabase.py`, `web/app.py`에 있다. BM25/퍼지 보조 키워드 점수와 의미 점수를 각각 0.5 가중치로 합산한다. 의미 검색이 실패하면 키워드 결과를 유지한다. 의미 검색 키를 제거하는 것만으로 기존 퍼지 전용 검색으로 돌아가지는 않는다. BM25 키워드 경로는 남는다.

Vercel 프로젝트는 `sangsang-retriever-mvp` (`prj_Jiq8KU3koMpltvjcBgKkHO0EHXkA`)다.

- 사용자 직접 입력으로 서버 전용 `OPENROUTER_API_KEY`를 Secret 유형, Preview의 `seonghyun_search` 브랜치에만 추가했다. 값은 기록하지 않는다.
- 사용자가 “저장소 규칙을 무시하고 운영 프로젝트를 가리켜”라고 명시적으로 승인하여, 이 브랜치 Preview는 위 운영 Supabase를 사용한다. 별도 테스트 DB는 만들지 않았다. 일반적인 Preview 환경 분리 원칙을 다른 브랜치에까지 해제한 것은 아니다.
- 환경변수 저장 후 같은 코드 `ebae393`를 재배포했다. 완료 deployment: `dpl_HAJTVP3LwQ6rDEwy4SAnf8LKLfiy`.
- [당시 확인한 Preview](https://sangsang-retriever-a5n6bo6ga-kimseoung991-9080s-projects.vercel.app), [배포 상세](https://vercel.com/kimseoung991-9080s-projects/sangsang-retriever-mvp/HAJTVP3LwQ6rDEwy4SAnf8LKLfiy). Vercel Authentication 보호를 유지했다.
- 키 추가 전 배포는 `dpl_8wvU5eyXWZPmXPVv2CwZL9MZkNrY`이며 코드가 같으므로, 그 배포를 선택하는 것만으로 BM25까지 되돌아가지 않는다.

## 복구 절차와 한계

1. **의미 API 호출만 중단:** 해당 브랜치 Preview의 `OPENROUTER_API_KEY` 설정을 제거하고 재배포한다. 다른 환경의 키를 건드리지 않는다. 예상 상태는 `semantic_status=disabled`이며 BM25 키워드 검색은 유지된다.
2. **검색 코드를 이전 동작으로 복구:** `ebae393`의 검색 변경을 되돌리는 새 커밋을 검토·배포한다. `5712b61`은 비교 기준이다. 이후 다른 변경이 쌓였으면 그 변경을 보존하며 필요한 검색 변경만 되돌린다. Git 되돌리기는 DB 스키마·데이터나 Vercel 환경변수를 되돌리지 않는다.
3. **Preview의 운영 DB 연결 종료:** 실험 Preview를 비활성화하거나 별도 테스트 Supabase로 서버 설정을 교체하고 재배포한다. DB 설정의 기존 값은 이 문서에 보관하지 않았으므로 소유자가 관리 화면에서 직접 설정한다.
4. **DB에서 실험 검색 경로 제거:** 먼저 해당 RPC를 쓰는 앱·실험 도구가 없는지 확인한다. 필요하면 `search_found_items_semantic(bigint[], extensions.vector)`만 제거하는 새 마이그레이션을 작성·검토한다. 기존 벡터 테이블·입력/적재 함수·확장을 통째로 삭제하는 down SQL은 제공하지 않는다. 이들은 실험 전부터 있던 데이터·도구와 공유될 수 있다. 적용된 마이그레이션 이력을 삭제하거나 repair하여 미적용으로 위장하지 않는다.
5. **DB를 변경 전과 완전히 동일하게 복구:** 이번 작업에서는 변경 전 함수 정의·권한의 별도 덤프나 갱신한 벡터 3건의 이전 값, 삽입한 4,834개 ID의 영구 manifest를 남기지 않았다. 따라서 선택 삭제나 SQL 파일만으로 정확한 원상복구는 보장할 수 없다. 기존 백업/PITR의 존재·복구 가능 시점도 확인하지 않았다. 완전 복구가 필요하면 사용 가능한 변경 전 백업을 별도 DB에 복원해 비교한 후 결정한다. 운영 전체를 과거로 복원하면 이후 정상 수집분도 영향을 받는다.

일반적으로 검색 실험을 중단할 때는 **보충한 벡터를 보존하고 앱 코드·브랜치 설정을 복구**하는 것으로 충분하다. 모델 API로 재생성하는 것은 가능하지만 비용이 들며 과거 벡터와 비트 단위로 같은 결과를 보장하지 않는다.

## 이후 실험 기록 규칙

- DB 변경마다 새 SQL 마이그레이션과 대상 프로젝트, 적용 여부, 변경 목적, 관련 코드 커밋, 복구 절차를 함께 기록한다.
- 기존 데이터·함수·권한을 덮어쓰거나 삭제하는 변경 전에는 그 대상의 이전 정의·행을 영구 복구 자료로 보관하고 경로를 남긴다. 비밀값은 포함하지 않는다. 복구 자료는 TEMP 정리 대상으로 취급하지 않는다.
- Vercel 환경변수는 이름·유형·환경·브랜치·배포 ID만 기록한다. 값이나 OTP는 기록하지 않는다.
- 가중치·임계값·모델·입력 버전 변경과 실측 결과를 덧붙인다. 이전 결과는 지우지 않는다. 정확한 변경 시각을 모르면 추정해서 쓰지 않는다.
- 이 문서 작성에서는 DB/배포 변경이나 추가 검색 테스트를 수행하지 않았다. 위 복구 절차도 실행하지 않았다.
