# 경찰청 습득물 API

경찰청 포털기관 습득물 API의 XML 데이터를 수집하는 핵심 코드입니다.

## 팀 개발 환경

Python 3.12와 [uv](https://docs.astral.sh/uv/)를 사용합니다. 저장소를 처음 받은 팀원은 프로젝트 루트에서 아래 명령만 실행하면 동일한 `.venv` 환경이 구성됩니다.

```powershell
uv sync --locked
```

- 의존성을 추가할 때: `uv add 패키지명`
- 개발 의존성을 추가할 때: `uv add --dev 패키지명`
- lock 갱신 없이 환경만 맞출 때: `uv sync --locked`
- Python 실행: `uv run python ...`
- 가상환경 직접 활성화가 필요할 때: `.venv\Scripts\Activate.ps1`

`uv.lock`은 반드시 Git에 포함하고 `.venv`와 `.env`는 포함하지 않습니다.
uv 캐시는 사용자 캐시 디렉토리를 사용하므로 프로젝트와 Git에 포함되지 않습니다.

## 프런트엔드 개발 환경

프런트엔드는 `frontend/`의 Vite + React + TypeScript 앱입니다. Node.js
20.19 이상 또는 22.12 이상을 사용합니다.

```powershell
cd frontend
npm install
npm run dev
```

Vite 개발 서버는 `/api` 요청을 `http://127.0.0.1:8000`의 FastAPI 서버로
전달합니다. 지도까지 확인하려면 프로젝트 루트의 `.env`에 카카오 키를 설정하고
별도 터미널에서 `uv run kakao-map`을 함께 실행하세요.

다른 프로젝트가 8000번 포트를 사용 중이면 `uv run kakao-map --port 8001`로
실행하고, 프로젝트 루트의 `.env.local`에
`RETRIEVER_DEV_API_TARGET=http://127.0.0.1:8001`을 설정하세요.
이 설정은 Vite 로컬 개발용이며 배포 API 경로에는 영향을 주지 않습니다.

배포용 프런트엔드 파일은 다음 명령으로 생성합니다.

```powershell
cd frontend
npm run build
```

빌드 결과는 `src/retriever_lost_found/web/static/`에 생성되며 FastAPI와 Vercel이
기존과 동일한 URL로 제공합니다. React 소스를 수정한 경우 생성된 정적 파일도 함께
갱신합니다.

## API 키

`.env.example`을 복사해 `.env`를 만들고 인증키를 입력합니다.

```dotenv
DATA_GO_KR_SERVICE_KEY=발급받은_인증키
```

## 실데이터 확인

아래 명령은 지정 기간의 첫 페이지에서 최대 10건을 출력합니다.

```powershell
uv run police-api --start 20260813 --end 20260814 --rows 10
```

전체 데이터 순회는 Python 코드에서 `create_client().iter_pages(...)`를 사용합니다.

중복을 제거한 최대 10,000건을 JSONL로 수집하려면 다음처럼 실행합니다.

```powershell
uv run police-api --start 20260801 --end 20260814 --rows 100 --limit 10000 --output data/found-items-10000.jsonl
```

## 카카오맵 장소 검색 데모

`.env`에 카카오디벨로퍼스에서 발급받은 JavaScript 키와 REST API 키를 추가합니다.

```dotenv
KAKAO_JAVASCRIPT_KEY=브라우저용_JavaScript_키
KAKAO_REST_API_KEY=서버용_REST_API_키
SUPABASE_URL=https://프로젝트_참조값.supabase.co
SUPABASE_SECRET_KEY=서버에서만_사용하는_Supabase_secret_키
```

카카오디벨로퍼스 앱 설정에서 카카오맵을 활성화하고, JavaScript SDK의 허용 도메인에
`http://localhost:8000`과 `http://127.0.0.1:8000`을 등록한 다음 실행합니다.

```powershell
uv run kakao-map
```

로컬에서는 Uvicorn이 FastAPI 앱을 실행하며, Vercel은 `api/index.py`의 동일한
FastAPI `app`을 진입점으로 사용합니다.

브라우저에서 `http://127.0.0.1:8000`을 열면 다음 기능을 사용할 수 있습니다.

- 카카오맵 JavaScript SDK 지도 표시
- 장소명 키워드 검색 후 검색 결과를 핀과 목록으로 표시
- 도로명·지번 주소를 좌표로 변환하고 핀으로 표시
- 유실물 연계기관 전체 위치를 숫자 클러스터와 개별 핀으로 표시
- 전국 경찰서·지구대·파출소 위치를 경찰청 관서로 통합해 노란색 핀으로 표시
- 지도를 확대하면 개별 기관, 축소하면 지역별 기관 수를 표시
- 개별 기관 핀의 배지에 Supabase에 저장된 현재 보관 물품 수를 표시
- 장소·주소 검색 좌표를 중심으로 반경 1km 원과 범위 내 연계기관만 표시
- 검색 결과가 여러 개면 목록에서 기준 위치를 변경하고 `전체 기관 보기`로 필터 해제
- 위치 검색 직후 반경 1km 기관들이 보관 중인 습득물 전체 목록 표시
- 개별 기관 핀을 누르면 해당 기관 물품만 다시 조회
- 위치 검색 후 동의어·BM25·Gemini Embedding 2 혼합 검색 활성화

카카오 REST API 키와 Supabase secret 키는 브라우저로 전달되지 않고 Python 서버에서만 사용됩니다.
지도 위치와 기관별 물품 수는 `map_locations_with_item_counts_json` Supabase RPC로 한 번에 읽습니다.
새 백엔드를 배포하기 전에 대상 DB에 `20261114063000_single_map_locations_rpc.sql`을 적용합니다.
따라서 로컬과 Vercel 모두 다음 서버 환경 변수가 필수입니다.

- `KAKAO_JAVASCRIPT_KEY`
- `KAKAO_REST_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` 또는 기존 프로젝트용 `SUPABASE_SERVICE_ROLE_KEY`
- `DATA_GO_KR_SERVICE_KEY`
- `CRON_SECRET`

검색 의미 확장에는 서버 전용 `OPENROUTER_API_KEY`를 선택적으로 설정합니다.
키가 없으면 키워드 검색만 실행하며, 테스트·CI에서는 빈 값으로 둡니다.
Preview에는 별도의 테스트 Supabase URL·서버 키와 임베딩 테이블을 준비하고
`supabase/migrations/20261111042456_scoped_bge_m3_semantic_search.sql`을 적용합니다.
운영 DB를 Preview에 연결하지 않습니다.

단, 2026-11-11 김성현이 명시적으로 승인한 `seonghyun_search` 검색 실험 Preview는
운영 Supabase를 사용합니다. 변경 내역과 중단·복구 절차 및 복구 한계는
[검색 실험 복구 기록](docs/measurements.md)에 남깁니다.

### 로컬 대화형 검색 API

로컬 `.env`의 서버 전용 `OPENROUTER_API_KEY`를 사용한다.
프런트를 빌드한 뒤 `uv run kakao-map --agent-search`로 실행하면 첫 화면의 검색 패널과 `/api/search-chat`이 대화형 검색에 연결된다.
OpenRouter의 GPT-6 Luna로 대화를 처리하고 카카오 장소 조회 및 설정한 Supabase의 기관·물품 검색을 호출한다.
로컬 검증에는 테스트 Supabase를 사용한다. Vite 개발 중에는 `/?search=chat`으로 열면 백엔드의 대화형 검색 API를 사용한다.
공용 대화 세션을 사용하려면 `20261120150000_agent_search_sessions.sql`을 먼저 적용한다. 기존 Supabase 서버 키를 사용하며 새 환경 변수는 없다. 유휴 30분 만료·삭제, 재시도와 취소 계약, 배포·복구 순서는 해당 문서에 있다. 운영 DB 적용과 배포는 별도 승인 후 진행한다.

목록 사진의 축소본은 `20261121180000_found_item_thumbnails.sql` 적용 후 전용 공개 Storage 버킷에 저장한다. 상세 사진은 원본을 사용하고, 축소본 조회나 로딩에 실패하면 원본으로 돌아간다. 수집 완료 시 기존 `CRON_SECRET`으로 별도 작업을 시작하며, Vercel이 제공하는 `VERCEL_ENV=production`과 `VERCEL_PROJECT_PRODUCTION_URL`이 있을 때만 운영 작업을 호출한다. 로컬·Preview는 운영 작업을 호출하지 않는다. 한 번에 제한된 수만 처리하고 임대를 사용하므로 중단 뒤 다음 수집 때 재개할 수 있다. 원본 URL 변경은 새 축소본을 만들고 물품 삭제가 확인된 뒤 축소본을 정리한다. 작업 기록이 실패해도 수집은 계속하며, 하루 간격으로 시작하는 대조가 누락된 생성·삭제를 다시 확인한다. 작업 스위치 `thumbnail_worker_guard.enabled`는 마이그레이션 직후 꺼져 있다. 디스크·Storage 여유와 수집 부하, 소량 처리·복구를 확인한 후 켜며 끄면 새 작업 임대를 중단한다. 이미 실행 중인 작업은 끝날 수 있다.
사용 흐름과 API 예시는 [대화형 검색 에이전트](docs/agent-search.md)를 참고한다. Vercel 진입점은 기본으로 대화형 검색을 켠다. Vercel 환경 변수 `AGENT_SEARCH=0`을 넣거나 `OPENROUTER_API_KEY`가 없으면 기존 검색 화면으로 돌아간다.

### 검색: 키워드 후보 BM25 + 반경 전체 1비트 의미 검색(Gemini Embedding 2)

검색은 기관 범위의 `keyword_found_item_candidates`로 작은 검색 필드만 읽고 BM25를 계산합니다.
종류는 서버가 동의어 사전(`synonyms.json`)의 종류 표현을 후보 단어로 넘겨 거르며, DB `kind_mask` 필터는 쓰지 않습니다(마이그레이션 `20261120120000`).
반복 정규화·전체 상세 JSON 조립·전체 범위 건수 조회는 제거했습니다.
이와 병렬로 OpenRouter → Google AI Studio Gemini Embedding 2 질의 벡터(`task: search result | query: …`, 1024차원)를 생성합니다.
질의 벡터가 나오면 `semantic_found_item_candidates_gemini2`가 반경 안 전체 물품을 Gemini 1비트 벡터 해밍 거리로 비교해 500건을 고르고,
그 500건만 Gemini half 벡터로 코사인을 다시 계산해 0.525 이상 상위 200건을 돌려줍니다. 의미 점수는 이 결과에서만 나옵니다(키워드에 없던 물품도 찾음).
최종 점수는 `0.75 × BM25/(BM25+1) + 0.25 × max(0, (cosine − 0.525) / 0.475)`이며, 키워드 결과는 모두 유지합니다.
물품 Gemini 벡터는 로컬 적재 도구로 채웠고, 일일 임베딩 크론도 Gemini Embedding 2(글만)로 새 물품을 채웁니다(2026-11-22, BGE-M3 표는 남겨 두되 더 채우지 않습니다).
키워드 일치 결과의 개수·날짜 상한은 없습니다. 50건씩 `search:offset` 커서로 이어 받고 전체 결과 수를 제공합니다.
초기 전체 기관 물품 수 `candidate_count`는 생략(null)합니다. 상세는 의미 검색 결과와 현재 표시할 페이지에 대해서만 읽습니다.
고비용 물품별 퍼지와 자동 오타 보정은 제거하고(오타는 따로 잡지 않음) BM25 bigram을 유지합니다.
물건 종류 목록(`search/item_kinds.json`)은 Supabase 분류 21개를 따르며, 검색어에 종류가 잡히면 그 종류의 표현만으로 후보를 고릅니다.
명확한 종류 충돌·아이폰과 다른 제조사 충돌은 제외하되, 함께 보관된 물품의 설명 근거도 인정합니다.
질의 벡터만 최대 128개/1시간 재사용하며 물품 응답은 캐시하지 않습니다.
전체 검색 대기 상한은 4.6초(초과 504), 질의 임베딩 예산은 4.0초입니다. 임베딩 실패 시 키워드 결과를 반환합니다.
`semantic_status`는 ok/unavailable/disabled/skipped(키워드 후보 없음)입니다.
키워드 후보에 없는 의미 단독 물품은 이 구조에서 복구하지 못합니다. 모든 질의의 미탐 0이나 첫 요청 시간 보장은 아닙니다.
실제 29,922건 범위/지갑은 키워드 1.954초·전체 3.743초, 28,620건/무선 이어폰은 1.000초·1.836초였습니다.
둘 다 의미 재정렬 성공이며 로컬 PC→실제 API/DB 각 1회입니다. 배포 성능·p95 측정과 구분합니다.
마이그레이션과 복구는 [실험 복구 기록](docs/measurements.md)을 따릅니다.

Vercel에서는 Production·Preview·Development 환경을 구분해 값을 설정하고, Preview가
의도치 않게 운영 Supabase에 연결되지 않게 관리하세요. `/api/institutions`의 공개 지도
응답은 Vercel CDN에서 5분간 캐시되며, `/api/found-items`는 캐시하지 않습니다.

기관 좌표와 물품 수는 로컬·배포 환경 모두 Supabase에서 조회합니다.

Vercel 함수는 [vercel.json](./vercel.json)의 `regions`에 따라 **서울(`icn1` =
AWS `ap-northeast-2`)** 에서 실행합니다. Supabase·경찰청 API·카카오 API가 모두
한국에 있으므로 함수도 같은 지역에 두어야 왕복 지연이 줄어듭니다. Vercel의 기본값은
`iad1`(미국 버지니아)이라 명시하지 않으면 모든 DB 왕복이 태평양을 건넙니다.
Hobby 요금제는 단일 리전만 허용하므로 `regions`에는 한 곳만 적습니다.

## 일일 데이터 동기화

두 경찰청 API의 습득물 전체를 상위분류 21개(`item_categories`)로 분류해 Supabase에 멱등 upsert합니다.
분류는 정확한 상위+하위 매핑 → 상위분류 정확 일치 → `기타물품` 순이며, 기타물품으로 저장한 건수는
`ingestion_runs.metadata.category_fallback_count`에 남습니다(PRD 4.5). 2026-11-20 전에는 6개 카테고리만 수집했습니다.
조회 종료일은 한국시간 오늘이며, 출처별 마지막 등록일과 어제 중 이른 날부터
겹쳐 조회합니다. 보존기간 시작일을 하한으로 두므로 매번 전체 6개월 구간을
다시 내려받지 않습니다. 오늘 등록분과 어제 늦게 반영된 물품을 함께 보완합니다.
API 조회와 모든 upsert가 성공한 출처만 오전 크론에서 달력 기준 6개월 시작일보다
오래된 데이터를 삭제합니다. 오후 크론은 삭제하지 않습니다. 예를 들어 9월 26일
빈 DB의 수집 대상은 3월 26일~9월 26일이고, 최신 DB는 9월 25일~26일을 재조회합니다.

보존기간 삭제는 5,000건씩 배치로 나눠 반복 실행합니다. 만료분 전량을 단일
DELETE로 지우면 `found_items`가 커졌을 때 Postgres `statement_timeout`에 걸려
(`57014`) 한 건도 지우지 못합니다. 한 실행의 상한은 40배치(20만 건)이며, 남은
만료분은 다음 오전 실행이 이어서 지웁니다. 수집이 멱등이므로 중간에 멈춰도 안전합니다.

```powershell
uv run sync-found-items
uv run sync-found-items --today 2026-08-28
```

### 과거 구간 백필 (전체 물품 확대 후 1회)

증분 수집은 마지막 등록일부터만 받으므로, 2026-11-20 전체 물품 확대 이전 기간의 새 분류 물품은
백필로 채웁니다. 마이그레이션 `20261120090000_all_item_categories.sql`을 운영에 적용한 뒤 실행합니다.

```powershell
uv run sync-found-items --backfill-from 2026-05-20 --backfill-to 2026-11-19
uv run sync-found-items --backfill-from 2026-05-20 --source police --chunk-days 7
uv run embed-found-items --until-complete
```

- 출처별로 7일씩(`--chunk-days`) 나눠 실행하고, 실행마다 `ingestion_runs`에 `trigger=manual_backfill`로 남깁니다.
  `business_date`는 실행한 날이고 정리 허용 회차로 기록해 오늘의 임베딩 회차 판정을 바꾸지 않습니다.
- DB에 이미 있는 물품은 다시 저장하지 않습니다(`metadata.skipped_existing_count`). 상세 조회도 새 물품에만 하며
  실행마다 60초·400회 예산을 새로 씁니다.
- 크론 시간대(KST 09·11·21·23시대)와 그 직전 20분에는 새 구간을 시작하지 않고 멈춥니다. 실패해도 멈춥니다.
  출력의 `resume`(출처·시작일)부터 다시 실행하면 됩니다. 이미 넣은 물품은 건너뛰므로 다시 실행해도 안전합니다.
- 보존기간 시작일 이전이나 오늘 이후 구간은 거부합니다. 백필이 끝나면 `embed-found-items --until-complete`로
  새 물품의 임베딩을 채웁니다. 이 과정은 물품의 색상·물품명 입력을 기존과 같은 외부 임베딩 API로 보냅니다.

동기화 중 상세 API를 자동 호출하거나 과거 물품의 상세정보를 전량 조회하지 않습니다.
기존 물품의 상세정보 보존과 검증된 동명 기관 규칙 적용은 DB 트리거가 처리합니다.
기관 코드가 없거나 규칙으로 해결되지 않는 물품은 `unmatched` 및 위치 NULL로 저장하고,
사람이 원문과 실제 기관을 확인한 뒤 검증된 별칭·기관 코드 규칙 또는 개별 물품을 수정합니다.
신규·갱신 건수는 이번 수집 물품 ID만 100개씩 조회하며, `unmatched_count`는
DB 트리거 적용 이후 최종 저장 결과를 기준으로 기록합니다.

동시 실행은 `ingestion_runs_one_running_per_source` 인덱스로 차단하며 실행 결과는
`ingestion_runs`에 기록됩니다. Vercel은 [vercel.json](./vercel.json)의
각 경로를 하루 한 번씩 호출합니다.

| 경로 | UTC | 한국시간 | 동작 |
| --- | --- | --- | --- |
| `/api/cron/partner-sync`, `/api/cron/police-sync` | 00:00 | 09시대 | 오늘까지 증분 수집 및 성공한 출처의 만료 정리 |
| `/api/cron/partner-sync-afternoon`, `/api/cron/police-sync-afternoon` | 12:00 | 21시대 | 오늘까지 증분 수집, 삭제 생략 |
| `/api/cron/embedding-sync` | 02:00 | 11시대 | 누락 임베딩 복구 |
| `/api/cron/embedding-sync-afternoon` | 14:00 | 23시대 | 누락 임베딩 복구 |

수집 종료 후에도 완료 트리거와 인증된 POST 콜백으로 임베딩을 연결합니다.
오전·오후 각각 두 출처의 종료를 기다리며 오전 이력으로 오후 완료를 대신하지 않습니다.
`embedding_runs`는 KST 날짜·입력 버전·회차(`run_slot`)마다 한 행을 기록합니다.
`morning`은 09시대, `afternoon`은 21시대 수집 종료 후 실행하며, 신규·누락이 0건이어도
별도의 완료 행을 남깁니다. 재시도·복구는 같은 회차의 행을 이어 쓰고 기존 벡터는 유지합니다.
두 복구 크론은 콜백 유실과 중단된 작업을 보완하며 추가 회차를 만들지 않습니다.
감사 로그의 `business_date`는 수집일(KST)이고 `metadata.cleanup_enabled`로
오전 정리/오후 수집 전용 실행을 구분합니다.
요청은 `Authorization: Bearer $CRON_SECRET`이 일치해야 실행됩니다.
Hobby도 프로젝트당 크론 100개를 지원하며 각 크론은 하루 한 번 실행할 수 있습니다.
지정한 한 시간 안에서 실행이 지연될 수 있으므로 정확한 시각을 보장하지 않습니다.
[Vercel Cron 제한](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing)

배포 순서: `20261113184917_distinguish_twice_daily_embedding_barrier.sql`을 먼저 적용한 뒤
앱과 크론 설정을 배포합니다. 기존 감사 기록은 오전 실행으로 취급하므로 먼저 적용해도
기존 앱과 호환됩니다. 이 마이그레이션은 완료 판정·복구 함수만 교체하며 물품·벡터를
수정하거나 초기화하지 않습니다.

### 임베딩 실행 기록 확인

회차별 기록에는 `record_embedding_runs_per_cycle` 마이그레이션도 필요합니다.
기존 앱의 RPC 호출과 호환되므로 앱 배포 전에 적용할 수 있습니다. 마이그레이션은
물품·벡터·기존 작업 ID/집계를 보존합니다. 실행 중인 임베딩 lease가 있으면 적용을
중단하므로 작업 종료 후 다시 적용합니다. 과거 일일 합산은 `legacy`로 남기고,
적용 당일의 기존 일일 행은 오전 기록으로 재사용하며 오후부터 별도 행을 만듭니다.
과거 오전·오후 결과를 추정해서 두 행으로 복제하지 않습니다.

Supabase Table Editor에서 **`embedding_runs_overview`** 뷰를 열고 `id` 오름차순으로
보면 번호가 `1, 2, 3…`으로 표시됩니다. `business_date`, `run_slot`, 예정 시각,
상태, 생성·시작·완료 시각(KST), 처리·재사용·실패·잔여 건수가 앞쪽에 배치됩니다.
표시용 `id`는 조회 시 계산되므로 행 삭제나 과거 날짜 작업 추가 시 바뀔 수 있습니다.
실제 작업을 식별할 때는 뒤쪽의 `run_id`를 사용합니다. 원본 테이블의 `id`는 재번호하지 않습니다.

- `scheduled_at_kst`는 예정 수집 회차이며 실제 임베딩 시작은 `started_at_kst`입니다.
  과거 작업의 시작 시각은 추정하지 않고 NULL로 둡니다.
- `succeeded_count`는 해당 회차에 저장한 벡터 수이며 `reused_count`를 포함합니다.
  `failed_count`는 누적 실패 시도 물품 수로, `remaining_count`와 다릅니다.
- `new_today_count`는 그날 처음 저장한 물품의 누적 스냅샷으로 회차별 신규 수가 아닙니다.
- 수동 CLI 실행은 `manual` 회차로 구분하며 자동 오전·오후 기록과 섞지 않습니다.
- 수집 실패·미호출 여부는 `ingestion_runs`에서 함께 확인합니다. 임베딩 성공만으로
  두 출처 수집의 성공을 판단하지 않습니다.

되돌릴 때는 이 마이그레이션 직전의 SQL 함수 정의와 날짜별 유일 제약이 한 쌍입니다.
회차별 행이 이미 쌓였다면 이전 날짜별 제약을 바로 복원할 수 없습니다. 기존 행을 보존해
집계·보관하는 별도 역방향 마이그레이션을 먼저 작성해야 하며, 과거 마이그레이션을 재실행하지 않습니다.

## 기관의 카카오 검색어 확인

검토 대상 기관에 저장한 `storage_locations.kakao_search`로 카카오 장소를 조회합니다.
값이 없는 기관은 기존 `name`을 사용합니다. 기관명이나 물품 연결 키는 바꾸지 않으며,
결과는 검토 후보입니다. 이 명령은 DB나 좌표를 수정하지 않습니다(PRD 4.5).

```powershell
uv run python scripts/search_storage_location_kakao.py 5052
```

검증된 기관 정보·검색어·매핑 규칙의 재현 SQL은
`supabase/repairs/20261107_reviewed_institution_repairs.sql`에 있습니다.
원래 기관 데이터를 적재하고 스키마 마이그레이션을 적용한 DB에서 수동 실행하는 파일이며,
해당 값은 운영 DB에 이미 반영했습니다. 물품 원문이나 물품 연결 상태는 이 파일로 갱신하지 않습니다.
서로 다른 DB에서도 같은 기관을 가리키도록 생성 ID 대신 출처·원본 기관 키로 연결합니다.

## 분실물 알림 메일(스케줄러)

검색 조건과 이메일로 알림을 예약하면, 등록 다음날부터 7일 동안 새로 들어온 습득물 중 유사한 물품을
메일로 알리고 마지막 날에는 종료·연장을 안내합니다(PRD 2.1-4, 4.2 P1, 4.3-E).
설계는 `docs/alert-scheduler/`(최신 결정은 설계 문서 15장), 화면 연결은 같은 폴더의 `frontend-handoff.md`에 있습니다.

- `/api/cron/alert-send`가 매일 UTC 02:00, 한국시간 오전 11시대에 한 번 돕니다.
  출처별 매칭(120초 안) → 마지막 날 종료 안내 → 발송 → 정리 순서입니다. 수집 크론은 알림을 건드리지 않습니다.
- 유사 판정은 운영 검색의 키워드 단계(동의어·BM25)와 같습니다. 같은 검색어로 같은 반경을 검색했을 때
  결과에 나오는 새 물품을 알립니다(`alerts/judge.py`).
- 최근 24시간 동안 하루 발송 상한(기본 450통)까지 보냅니다.
- 메일 속 링크는 `/alerts/confirm`·`/alerts/extend`·`/alerts/cancel` 화면을 엽니다.
- 운영 DB에는 `supabase db push`로 `20261115090000_create_alert_scheduler.sql`을 적용합니다.
- 메일 설정이 하나라도 없으면 알림 예약 API(`/api/alerts*`)는 503을 돌려줍니다.
  Preview 환경에는 메일 설정을 넣지 않아 실제 메일이 나가지 않게 합니다.

발신 수단은 **Google 메일(Gmail 앱 비밀번호, SMTP)** 로 확정했습니다(2026-11-22).

서버 전용 환경 변수입니다. 브라우저로 전달하지 않습니다.

- `MAIL_FROM` — 보내는 주소. 서비스 전용 Gmail 주소와 같게 씁니다. 예: `리트리버 <서비스 Gmail 주소>`
- `MAIL_SMTP_HOST` — `smtp.gmail.com`
- `MAIL_SMTP_PORT` — `465`(SSL) 또는 `587`(STARTTLS)
- `MAIL_SMTP_USERNAME` — 서비스 전용 Gmail 주소
- `MAIL_SMTP_PASSWORD` — 그 계정의 앱 비밀번호 16자리, 공백 없이
- `PUBLIC_BASE_URL` — 메일 속 링크 주소의 앞부분. 로컬은 `http://127.0.0.1:8000`
- `ALERT_SECRET` — 링크 토큰과 IP 해시에 쓰는 무작위 문자열
- `ALERT_DAILY_SEND_LIMIT` — 선택. 기본 450

**Gmail 준비** — 서비스 전용 Gmail 계정에 2단계 인증을 켠 뒤 https://myaccount.google.com/apppasswords
에서 앱 비밀번호를 만듭니다. 계정 비밀번호를 바꾸면 앱 비밀번호가 무효가 되어 메일이 멈춥니다.
`ALERT_SECRET`은 다음 명령으로 만듭니다. 값을 바꾸면 이미 보낸 메일의 링크가 모두 무효가 됩니다.

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

배포 전에는 DB 없이 메일 한 통만 보내 발송 설정과 스팸함 분류를 확인합니다.
실제 메일이 나가므로 받는 주소를 확인한 뒤 실행합니다.

```powershell
uv run python -m retriever_lost_found.alerts.send_test --to 받을주소@example.com
```

## 프로젝트 구조

```text
api/                         Vercel FastAPI 진입점
frontend/                    Vite·React·TypeScript 프런트엔드 소스
src/retriever_lost_found/
├─ config.py                 환경 변수 로딩
├─ web/                      웹 앱·Cron·정적 UI
├─ integrations/             Kakao·Supabase·경찰청 API 연동
├─ ingestion/                카테고리 선별·수집·저장·동기화
└─ search/                   동의어·BM25·1비트 의미 검색
supabase/migrations/         재현 가능한 DB 스키마
tests/                       기능 영역별 테스트
```


2026-11-14 운영 검색 전환: 사용자 승인으로 검색어 경로를 최신 동의어·BM25·상위 200건 BGE-M3 재정렬·50건 페이지 반환으로 변경한다. 기존 fuzzy.py와 물품별 퍼지 순위 계산은 제거한다. 질의 측의 작은 사전 오타 보정은 유지한다. 일일 v2 half 임베딩 자동화는 기존 main 구현을 유지한다. LUNA/LLM 실험은 포함하지 않는다. 과거의 “운영 퍼지 유지/검색 실험 브랜치 한정” 서술은 당시 기록이며 이번 변경으로 대체한다.

운영 복구: 검색 앱을 이전 배포로 되돌려도 v2 half 벡터와 일일 생성 작업은 유지한다. 과거 검색 SQL·rollback 파일을 운영 DB에 자동 실행하지 않는다. 특히 v2 초기화 migration은 재실행하지 않는다. 전환 전 전체 v1 벡터 백업은 없으며 v1 전체 복원은 보장하지 않는다.
