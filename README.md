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
- 개별 기관 핀의 배지에 Supabase에 저장된 현재 6개 카테고리 물품 수를 표시
- 장소·주소 검색 좌표를 중심으로 반경 1km 원과 범위 내 연계기관만 표시
- 검색 결과가 여러 개면 목록에서 기준 위치를 변경하고 `전체 기관 보기`로 필터 해제
- 위치 검색 직후 반경 1km 기관들이 보관 중인 습득물 전체 목록 표시
- 개별 기관 핀을 누르면 해당 기관 물품만 다시 조회
- 위치 검색 후 물품명·색상·카테고리·설명 대상 퍼지 검색 활성화

카카오 REST API 키와 Supabase secret 키는 브라우저로 전달되지 않고 Python 서버에서만 사용됩니다.
지도 위치와 기관별 물품 수는 `map_locations_with_item_counts` Supabase RPC에서 함께 읽습니다.
따라서 로컬과 Vercel 모두 다음 서버 환경 변수가 필수입니다.

- `KAKAO_JAVASCRIPT_KEY`
- `KAKAO_REST_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` 또는 기존 프로젝트용 `SUPABASE_SERVICE_ROLE_KEY`
- `DATA_GO_KR_SERVICE_KEY`
- `CRON_SECRET`

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

두 경찰청 API에서 6개 서비스 카테고리만 선별해 Supabase에 멱등 upsert합니다.
각 출처의 마지막 등록일을 다시 포함해 누락된 늦은 등록 건을 보충하므로 매일 전체
10일·20일 구간을 다시 내려받지 않습니다. API 조회와 모든 upsert가 성공한 출처만
연계기관 10일, 경찰관서 20일 보존기간 밖의 데이터를 삭제합니다.

보존기간 삭제는 5,000건씩 배치로 나눠 반복 실행합니다. 만료분 전량을 단일
DELETE로 지우면 `found_items`가 커졌을 때 Postgres `statement_timeout`에 걸려
(`57014`) 한 건도 지우지 못합니다. 한 실행의 상한은 40배치(20만 건)이며, 남은
만료분은 다음 실행이 이어서 지웁니다. 수집이 멱등이므로 중간에 멈춰도 안전합니다.

```powershell
uv run sync-found-items
uv run sync-found-items --today 2026-08-28
```

동시 실행은 `ingestion_runs_one_running_per_source` 인덱스로 차단하며 실행 결과는
`ingestion_runs`에 기록됩니다. Vercel은 [vercel.json](./vercel.json)의
`/api/cron/partner-sync`와 `/api/cron/police-sync`를 매일 UTC 00:00,
한국시간 오전 9시에 각각 호출합니다. 두 엔드포인트는 해당 수집원의 당일 등록분만
증분 동기화하며, 누락 기간 보완은 로컬 `sync-found-items` 명령으로 실행합니다.
요청은 `Authorization: Bearer $CRON_SECRET`이 일치해야 실행됩니다. Vercel Hobby
요금제에서는 지정한 한 시간 안에서 실행 시각이 지연될 수 있습니다.

## 프로젝트 구조

```text
api/                         Vercel FastAPI 진입점
src/retriever_lost_found/
├─ config.py                 환경 변수 로딩
├─ web/                      웹 앱·Cron·정적 UI
├─ integrations/             Kakao·Supabase·경찰청 API 연동
├─ ingestion/                카테고리 선별·수집·저장·동기화
├─ search/                   퍼지 검색과 순위화
└─ tools/                    좌표 생성·갱신용 명령줄 도구
supabase/migrations/         재현 가능한 DB 스키마
tests/                       기능 영역별 테스트
```
