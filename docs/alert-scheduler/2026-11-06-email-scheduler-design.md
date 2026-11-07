# ③ 스케줄러(분실물 알림 예약) 설계

> 작성 2026-11-06 · 담당 권희준 · 근거 PRD 2.1-4, 4.2 P1 "신규 등록 알림", `docs/team-roles.md` ③, `AGENTS.md` 2장 10번 부연
> 상태: 설계 확정 대기. **유사 판정만 ⑥(검색 방식 확정) 결론 뒤에 구현**하고, 나머지는 지금 구현한다. 알림 메일은 이번 스프린트(~11-09) 안에 보낸다.
> 이 문서는 담당자 지시(2026-11-06)로 저장소 `docs/alert-scheduler/`에 둔다(`AGENTS.md` 6장 보관 규칙의 예외). 확정된 결정은 구현 PR에서 `docs/team-roles.md`에 옮긴다.

---

## 1. 목표와 범위

**목표** — 사용자가 찾는 물건이 없을 때 검색 조건과 이메일을 저장해 두면, 예약 기간 동안 매일 습득물이 갱신된 뒤 그 조건과 유사한 물품이 새로 들어왔는지 확인해 이메일로 알린다. 공공 습득물 DB를 다시 *탐색*해 주는 기능이며 분실 신고·직접 매칭이 아니다(PRD 2.2 비목표 1). 메일 문구는 "유사한 물품이 새로 등록됐습니다"로 쓰고 본인 물건이라고 단정하지 않는다.

**이번 범위**

- 예약 API(생성), 링크 API(요약·확인·연장·취소)
- 확인 메일·알림 메일·만료 안내 메일 발송(Gmail SMTP)
- 수집 크론 끝의 매칭 단계(후보 조회까지, 판정은 ⑥ 뒤)
- 발송 전용 크론(KST 11시대): 만료 안내, 발송, 정리
- 테이블·DB 함수 마이그레이션, 테스트, 환경 변수·문서 갱신

**범위 밖**

| 항목 | 이유·담당 |
| --- | --- |
| 예약 입력 화면, 메일 링크 페이지 | 강지현. 핸드오프는 `frontend-handoff.md` |
| 유사 판정 방법 | ⑥(김성현) 결론을 그대로 적용. 판정 구현은 ⑥ 직후(권희준) |
| 운영자용 크론 실패 알림(PRD Q6) | 스프린트 밖. 같은 발송 모듈(`EmailSender`)을 재사용한다 |
| 도메인·전용 발송 서비스·대기열 서비스 | 확장 단계(11장) |

---

## 2. 결정 사항

| 항목 | 결정 | 날짜 |
| --- | --- | --- |
| 회원가입 | 없음. 이메일만 받고 확인 링크를 눌러야 예약이 켜진다 | 11-05 팀 |
| 저장할 조건 | 검색어(1~30자 필수) + 기준 지점 좌표 + 반경(1·3·5km) + 장소 이름. 기관 목록은 저장하지 않고 비교할 때마다 다시 계산한다 | 11-05 팀 |
| 기관만 보던 중의 예약 | 반경 전체로 저장한다 | 11-06 |
| 비교 대상 | 예약 이후 새로 들어온 물품만. 한 번 알린 물품은 다시 보내지 않는다 | 11-05 팀 |
| 유사 기준 | ⑥에서 정한 검색 방법을 그대로 적용한다. 검색과 알림의 **판정 방법은 같게**, **발송 규칙은 따로** 둔다 | 11-06 |
| 실행 시점 | 매칭은 각 수집 크론 끝(출처별), 발송은 발송 전용 크론(KST 11시대) | 11-06 |
| 발신 수단 | Gmail 앱 비밀번호(SMTP). 설정은 환경 변수. 서비스 전용 Gmail 계정 권장 | 11-06 |
| 하루 발송 상한 | 최근 24시간 450통. 확인·알림·만료 안내를 모두 센다 | 11-06 |
| 알림 메일 | 예약당 하루 최대 1통, 물품 최대 5개 + "그 밖에 N개" | 11-06 |
| 만료 | 등록한 날(KST 날짜) + 7일의 11:00 KST. 예: 11-06 등록 → 11-13 11:00 | 11-06 (5.3) |
| 연장 | 한 번에 +7일, 최대 3회(최장 4주). 메일 속 링크로만 | 11-06 |
| 메일 속 링크 | 모든 메일에 취소, 알림 메일에 연장, 만료 전날 안내 메일(연장 링크 포함) | 11-06 |
| 개인정보 | 예약이 끝나면(만료·취소·미확인) 이메일과 IP 해시만 지우고 행은 남긴다(전환율 집계용) | 11-06 |
| 남용 방지 | 같은 이메일의 진행 중(`pending`·`active`) 예약 최대 3개, 같은 IP 최근 24시간 5건 | 11-06 |
| 화면 공개 | 판정이 붙은 뒤 사용자에게 연다 | 11-06 |

---

## 3. 전체 구조

```
[예약]  화면(강지현) → POST /api/alerts
          → 예약 '대기' 저장 → 확인 메일 즉시 발송(450통 계산 포함)
        메일 링크 → 링크 페이지(강지현) → 버튼 → POST /api/alerts/confirm → '활성'

[매일 KST 09시대]  /api/cron/partner-sync, /api/cron/police-sync   ← 기존 크론
          수집(변경 없음) → 감사 로그 마감
          → (try, 실패해도 수집 결과·응답 코드 불변) 매칭 단계 — 이 출처만
               예약마다: 후보 조회(DB) → 유사 판정(⑥) → 예약의 '대기 알림 메일'에 물품 추가

[매일 KST 11시대]  /api/cron/alert-send   ← 새 크론 "0 2 * * *"
          ① 만료 안내 메일 넣기 → ② 발송(450통 한도) → ③ 정리(만료·미확인 종료, 이메일 삭제)
```

**11시로 둔 이유** — Hobby 크론은 지정한 시간대 안 아무 때나 돈다. 수집은 09:00~09:59에 시작해 최대 5분이 걸리므로, 10시로 두면 드물게 수집이 끝나기 전에 발송이 돌 수 있다. 11시면 겹칠 수 없다. 메일은 11:00~11:59에 도착한다.

### 3.1 모듈

```
src/retriever_lost_found/alerts/
├─ settings.py     환경 변수 읽기. 메일 설정이 없으면 None → API는 503
├─ sender.py       EmailSender 인터페이스, SmtpEmailSender(smtplib, 465=SSL / 587=STARTTLS),
│                  오류 분류(DailyLimitExceeded · PermanentSendError · TemporarySendError)
├─ links.py        링크 토큰 생성·검증(HMAC), IP 해시
├─ store.py        SupabaseAlertStore — REST·RPC 호출(기존 integrations/supabase.py 방식)
├─ reservations.py 예약 생성·확인·연장·취소 규칙
├─ matching.py     find_new_similar_items(경계) · run_match_pass(source, deadline)
├─ dispatch.py     run_send_pass(deadline): 만료 안내 → 발송 → 정리
└─ templates.py    메일 제목·본문(text + HTML, 사용자 입력은 escape)
src/retriever_lost_found/web/
├─ alerts.py       APIRouter — /api/alerts*
├─ cron.py         _run_source 끝에 매칭 단계 연결, /api/cron/alert-send 추가
└─ app.py          alerts 라우터 등록
```

### 3.2 ⑥과의 경계

```python
@dataclass(frozen=True)
class MatchResult:
    items: list[tuple[dict, float]]   # (물품, 점수) — 점수는 메일 정렬에 쓴다
    checked_until: datetime          # 이번에 어디까지 확인했는지(커서)

def find_new_similar_items(store, judge, reservation, source) -> MatchResult: ...
```

- 기본 구현: DB 함수 `alert_candidate_items`로 후보를 받고 `judge`로 거른다.
- ⑥이 Python 판정이면 `judge`만 넣는다. 판정을 DB로 옮겨야 하면 이 함수의 속을 통째로 바꾼다. 바깥(매칭 단계, 발송)은 바뀌지 않는다.
- **⑥ 전에는 `judge`가 없으므로 매칭 단계는 `skipped`로 기록하고 커서를 움직이지 않는다.** 테스트는 가짜 판정기를 쓴다.

---

## 4. 데이터 모델

마이그레이션 하나(`supabase/migrations/<timestamp>_create_alert_scheduler.sql`). **모든 테이블에 RLS를 켜고 `anon`·`authenticated` 권한을 회수하고 `service_role`에만 권한을 준다(절대 규칙 6).** DB 함수도 `service_role`에만 `execute`를 준다.

예약을 참조하는 FK(`alert_match_cursors`·`alert_messages`·`alert_notified_items`)와 `alert_notified_items.message_id`는 모두 `on delete cascade`다. 예약 행을 지우는 경우는 확인 메일 발송 실패·한도 초과(5.4)뿐이고, 대기 메일을 지우는 경우는 예약이 끝났을 때뿐이다.

### 4.1 `alert_reservations` — 예약

| 컬럼 | 타입·제약 | 설명 |
| --- | --- | --- |
| `id` | bigint identity PK | |
| `email` | text null | 끝나면 NULL |
| `query` | text not null, `char_length(btrim(query)) between 1 and 30` | PRD 3.3 30자 |
| `place_name` | text not null | 메일 문구용 |
| `position` | geography(point, 4326) not null | 기준 지점 |
| `radius_m` | integer not null, `in (1000, 3000, 5000)` | |
| `status` | text not null, `in ('pending','active','expired','cancelled','unconfirmed_expired')` | |
| `link_nonce` | text not null | 링크 토큰 HMAC에 섞는 무작위 값(5.1) |
| `ip_hash` | text null | 끝나면 NULL |
| `consent_at` | timestamptz not null | 동의 시각 |
| `created_at` | timestamptz not null default now() | **비교 기준 시각** |
| `confirmed_at` | timestamptz null | |
| `expires_at` | timestamptz not null | 5.3 |
| `extension_count` | smallint not null default 0, `between 0 and 3` | |
| `ended_at` | timestamptz null | |

- 제약: `status in ('pending','active')`이면 `email is not null`, 그 밖이면 `email is null and ip_hash is null` — 끝난 예약에 개인정보가 남을 수 없게 DB가 막는다.
- 인덱스: `(status, expires_at)`, `(email) where status in ('pending','active')`, `(ip_hash, created_at) where ip_hash is not null`

### 4.2 `alert_match_cursors` — 출처별로 어디까지 확인했는지

| 컬럼 | 설명 |
| --- | --- |
| `reservation_id` | FK |
| `item_source_code` | FK `item_sources` |
| `checked_until` | timestamptz. 처음 값은 예약의 `created_at` |

PK `(reservation_id, item_source_code)`.

**필요한 이유** — 커서가 없으면 판정에 걸리지 않은 물품이 매일 다시 후보가 되어 예약 기간 내내 후보가 쌓인다(4주면 4주치). 커서로 "지난번 확인 이후 처음 들어온 물품"만 보게 한다. 커서는 그 예약의 매칭이 끝까지 처리됐을 때만 움직이므로, 하루 실패해도 다음 날 두 날치를 함께 본다.

### 4.3 `alert_messages` — 보낼 메일 대기열

| 컬럼 | 설명 |
| --- | --- |
| `id` | bigint identity PK |
| `reservation_id` | FK |
| `kind` | `in ('confirm','match','expiry_notice')` |
| `status` | `in ('pending','sending','sent','failed')` |
| `expiry_for` | timestamptz null — 만료 안내가 어느 만료일에 대한 것인지 |
| `attempts` | smallint default 0 |
| `claimed_at` · `sent_at` | timestamptz null |
| `last_error` | text null — 비밀값을 가린 뒤 저장 |
| `created_at` | timestamptz default now() |

- 유일 조건:
  - `(reservation_id) where kind = 'match' and status = 'pending'` — **예약마다 대기 알림 메일은 항상 1통.** partner·police가 같은 메일에 물품을 더한다
  - `(reservation_id, expiry_for) where kind = 'expiry_notice'` — 같은 만료일 안내는 한 번
  - `(reservation_id) where kind = 'confirm'`
- 인덱스: `(status, kind, created_at)`, `(sent_at) where status = 'sent'` — 24시간 발송 수 계산

### 4.4 `alert_notified_items` — 알린 물품

| 컬럼 | 설명 |
| --- | --- |
| `reservation_id` | FK |
| `found_item_id` | FK `found_items` on delete cascade(보존기간 정리와 충돌하지 않게) |
| `message_id` | FK `alert_messages` |
| `score` | real null — 메일 안 정렬용 |
| `created_at` | |

PK `(reservation_id, found_item_id)` — 한 번 알린 물품은 다시 들어갈 수 없다.

### 4.5 `alert_runs` — 실행 기록(감사 로그 불변식)

`kind in ('match','send')`, `item_source_code`(매칭만), `started_at`, `finished_at`, `status in ('running','succeeded','partial','failed','skipped')`, 건수 컬럼(`reservations_checked`, `reservations_matched`, `items_queued`, `expiry_notices_queued`, `messages_sent`, `messages_deferred`, `messages_failed`, `reservations_expired`, `reservations_unconfirmed_closed`), `error_message`, `metadata jsonb`.

### 4.6 DB 함수

여러 쓰기를 한 번에 처리해야 하는 곳만 DB 함수로 둔다. 단순 조회·한 행 갱신은 REST로 한다.

| 함수 | 하는 일 |
| --- | --- |
| `create_alert_reservation(...)` | 이메일 3개·IP 하루 5건 제한을 확인하고 예약과 출처별 커서를 만든다. 결과는 `created` · `email_limit` · `ip_limit` |
| `cancel_alert_reservation(id)` | 취소, 이메일·IP 해시 삭제, 대기 메일 삭제를 한 번에 |
| `alert_candidate_items(reservation_id, source)` | 반경 안 활성 기관(`ST_DWithin(position, 기준점, radius_m, false)` — **구면 계산으로 화면 Haversine과 경계를 맞춘다**) · 이 출처 · `storage_location_id` 있음(매칭된 물품만, 절대 규칙 5) · `first_seen_at > checked_until` · 아직 안 알림. 함께 `checked_until`로 쓸 DB 시각을 돌려준다 |
| `record_alert_match(reservation_id, source, checked_until, items)` | 알린 물품 기록 + 대기 알림 메일에 붙이기(없으면 생성) + 커서 이동을 한 번에. 물품이 없어도 커서는 움직인다 |
| `enqueue_alert_expiry_notices()` | 만료까지 36시간 이하로 남은 활성 예약에 만료 안내를 넣는다(5.3) |
| `claim_alert_messages(limit, daily_cap, message_id?)` | `pg_advisory_xact_lock`으로 순서를 세우고, 10분 넘게 방치된 `sending`을 `pending`으로 되돌리고, `daily_cap − 최근 24시간 sent − sending`만큼 `pending`을 우선순위(확인 → 알림 → 만료 안내, 오래된 순) `for update skip locked`로 집어 `sending`으로 바꾼다. `message_id`를 주면 그 한 통만(확인 메일용) |
| `close_ended_alert_reservations()` | 만료 시각이 지난 활성 예약 → `expired`, 확인 없이 24시간 지난 대기 예약 → `unconfirmed_expired`. 둘 다 이메일·IP 해시 삭제, 대기 메일 삭제 |

---

## 5. 예약과 링크

### 5.1 링크 토큰

- 토큰 = `<예약 id>.<HMAC-SHA256(ALERT_SECRET, "link:" + id + ":" + link_nonce)의 앞 32자>`
- DB에는 토큰을 저장하지 않는다. 알림·만료 안내 메일을 보낼 때마다 다시 계산해 링크에 넣는다. 원래 토큰의 해시만 저장하면 나중 메일에 링크를 넣을 수 없기 때문이다.
- 검증: id로 행을 찾아 다시 계산한 값과 `hmac.compare_digest`로 비교한다. 예약이 끝나면 상태 검사에서 막힌다.
- `ALERT_SECRET`을 바꾸면 이미 보낸 링크가 모두 무효가 된다(교체할 때 주의).

### 5.2 IP 해시

`HMAC-SHA256(ALERT_SECRET, "ip:" + IP)`. IP는 Vercel이 넣어 주는 `x-real-ip`(없으면 `x-forwarded-for` 첫 값)에서 읽는다 — 구현 때 Vercel 문서로 헤더를 확인한다.

### 5.3 만료 시각

- `expires_at` = 등록한 날(KST 날짜) + 7일의 **11:00 KST**. 연장하면 +7일(여전히 11:00).
  - 예: 11-06 15시 등록 → 11-13 11:00 만료
- 이렇게 맞춘 이유:
  - 만료 시각이 발송 크론 시각과 같아서, 만료되는 그 실행에서 정리가 일어난다. 이메일이 만료 후 최대 약 1시간 안에 지워진다.
  - 만료일 아침(09시대) 매칭분도 그날 11시 발송에 실린다(발송 → 정리 순서, 6.2).
  - 만료 안내가 정확히 만료 전날 실행에 걸린다(남은 시간 약 24시간 — "36시간 이하" 조건, 크론 시각 오차 ±1시간을 견딘다).
- 매칭·링크 처리는 상태뿐 아니라 `expires_at > now()`도 확인한다.

### 5.4 API

모든 `/api/alerts*` 응답은 `Cache-Control: no-store`. 오류 메시지는 한국어.

**`POST /api/alerts`**

```json
요청 { "email": "...", "query": "에어팟", "latitude": 37.5547, "longitude": 126.9707,
       "radius_m": 3000, "place_name": "서울역", "consent": true }
응답 202 { "status": "pending", "message": "확인 메일을 보냈습니다. 24시간 안에 링크를 눌러 주세요." }
```

처리 순서: 입력 검증 → `create_alert_reservation` → 확인 메일 행 생성 → `claim_alert_messages(message_id=…)` → 발송 → `sent` 기록.

| 코드 | 언제 | 예약 |
| --- | --- | --- |
| 400 | 이메일 형식(`@` 하나, 공백 없음, 254자 이하)·검색어 1~30자·반경 값·좌표 범위·동의 | 만들지 않음 |
| 429 | 이메일 3개 / IP 최근 24시간 5건 / 450통 한도로 확인 메일을 집지 못함 | 한도 초과 시 방금 만든 예약 삭제 |

이메일은 앞뒤 공백을 지우고 소문자로 바꿔 저장한다. 대소문자만 바꿔 이메일 제한을 피하지 못하게 하려는 것이다.
| 502 | Gmail 발송 실패 | 방금 만든 예약 삭제 |
| 503 | Supabase 또는 메일 설정 없음(Preview 등) | 만들지 않음 |

**`GET /api/alerts/summary?token=…`** — 링크 페이지에 보여 줄 요약. **상태를 바꾸지 않는다.**

```json
{ "status": "active", "place_name": "서울역", "radius_m": 3000, "query": "에어팟",
  "expires_at": "2026-11-13T11:00:00+09:00", "extensions_left": 3 }
```

**`POST /api/alerts/confirm` · `/extend` · `/cancel`** — 요청 `{ "token": "..." }`

| 동작 | 성공 조건 | 결과 | 거절 |
| --- | --- | --- | --- |
| 확인 | `pending`이고 `created_at`부터 24시간 이내 | `active`, `confirmed_at` | 24시간 지남 · 이미 끝남. 이미 `active`면 성공으로 응답("이미 켜져 있습니다") |
| 연장 | `active`, `expires_at > now()`, `extension_count < 3` | `expires_at` +7일, 횟수 +1 | 3회 다 씀 · 이미 끝남 |
| 취소 | `pending` 또는 `active` | `cancel_alert_reservation` | 이미 끝남(성공으로 응답) |

토큰이 틀리면 400 "링크가 올바르지 않습니다".

---

## 6. 크론

### 6.1 매칭 단계 — 수집 크론 끝

- `cron.py` `_run_source`에서 `run_incremental_sync(source)` 뒤에 `try`로 실행한다. 수집 코드(`ingestion/service.py`)는 고치지 않는다.
- 수집 결과가 `succeeded` 또는 `partial`(= 저장까지 끝남)이고 이번 수집에서 새로 저장된 물품이 1건 이상(`source.inserted > 0`)일 때만 돈다. 그 밖에는 DB를 부르지 않고 건너뛴다. 새 물품이 없으면 비교할 것도 없고, 시간 예산 때문에 밀린 예약은 다음 날 커서로 따라잡는다.
- 시간 예산: **요청 시작부터 260초.** 예약을 하나 처리할 때마다 확인하고, 넘으면 멈춘다.
- 처리 순서: 이 출처의 커서가 오래된 예약부터(멈춘 날 밀린 예약이 다음 날 먼저 처리된다).
- 예약 하나: `find_new_similar_items` → `record_alert_match`.
- 응답: 본문과 상태 코드 모두 지금 그대로 둔다(기존 `tests/web/test_cron_endpoint.py`가 본문 전체를 비교한다). 알림 단계 결과와 예외는 `alert_runs`에만 남고 `ingestion_runs`는 건드리지 않는다(출처 독립·보존 우선, `AGENTS.md` 4장).

### 6.2 발송 크론 — `/api/cron/alert-send` (`"0 2 * * *"`)

`Authorization: Bearer $CRON_SECRET` 검증(절대 규칙 8) — 기존 `_authorized` 재사용. 시간 예산 260초.

1. **만료 안내 넣기** — `enqueue_alert_expiry_notices()`
2. **발송** — `claim_alert_messages`로 집어 SMTP 연결 하나로 차례로 보낸다.
   - 보내기 직전 예약 상태가 `pending`·`active`인지 재확인하고, 아니면(취소 등) 메일을 삭제한다. **만료 시각은 여기서 보지 않는다** — 만료일 아침 매칭분을 그날 보내기 위해서다. 만료된 예약의 남은 메일은 3단계 정리에서 지워진다.
   - 연장 링크는 남은 횟수가 있고 아직 만료 전일 때만 넣는다.
   - 알림 메일 본문은 `alert_notified_items`(이 메일에 묶인 물품)를 점수 높은 순, 같으면 최근 등록 순으로 5개 + "그 밖에 N개".
   - 시간 예산을 넘으면 집어 온 나머지를 `pending`으로 되돌린다.
3. **정리** — `close_ended_alert_reservations()`. 발송 뒤에 두어 만료일 아침 매칭분이 먼저 나간다.

### 6.3 발송 오류

| 상황 | 처리 |
| --- | --- |
| `550 5.4.5` 하루 한도 초과 | 즉시 멈춤, 나머지 `pending` |
| 그 밖의 5xx(없는 주소 등) | 그 메일만 `failed`, 재시도 없음 |
| 4xx·네트워크 오류 | `pending`, `attempts` +1. 3회면 `failed` |
| `sending`인 채 10분 넘음 | 다음 `claim`에서 `pending`으로. 보낸 직후 죽은 경우 드물게 중복 발송될 수 있음(감수) |
| SMTP 로그인 실패(앱 비밀번호 무효 등) | 그 실행 전체 `failed`, 메일은 모두 `pending` 유지. `alert_runs`에 기록 |

---

## 7. 메일

| 종류 | 제목 | 본문 |
| --- | --- | --- |
| 확인 | `[리트리버] 분실물 알림 예약을 확인해 주세요` | 조건 요약(장소·반경·검색어), 확인 링크, "24시간 동안 유효", "직접 신청하지 않았다면 무시하세요", 취소 링크 |
| 알림 | `[리트리버] 저장한 조건과 유사한 물품이 새로 등록됐습니다` | 조건 요약, 물품 최대 5개(물품명·분류·색상·등록일·보관기관 이름·전화번호) + "그 밖에 N개", 리트리버 링크, 만료일, 연장 링크(남은 횟수, 0이면 생략), 취소 링크 |
| 만료 안내 | `[리트리버] 분실물 알림 예약이 내일 끝납니다` | 조건 요약, 만료 시각, 연장 링크(남은 횟수가 0이면 "새로 예약하기" 안내), 취소 링크 |

- text + HTML 두 형식. **검색어·장소 이름은 사용자 입력이므로 HTML escape한다.** 제목에는 사용자 입력을 넣지 않는다(헤더 주입 방지).
- 링크 앞부분은 `PUBLIC_BASE_URL`. 링크 페이지 경로(`/alerts/confirm` 등)는 프런트와 합의한다.
- 발신자 `MAIL_FROM`(예: `리트리버 <서비스 Gmail>`).

---

## 8. 보안·개인정보

- 비밀값(`MAIL_SMTP_PASSWORD`, `ALERT_SECRET`, `SUPABASE_SECRET_KEY`, `CRON_SECRET`)은 서버 전용. 변수 이름은 `VITE_`로 시작하지 않는다(키 격리, 절대 규칙 4).
- 오류 메시지는 기존 `_safe_error`처럼 비밀값을 가린 뒤 저장·응답한다. 특히 SMTP 오류 문구에 앱 비밀번호가 섞이지 않게 한다.
- 끝난 예약에 이메일·IP 해시가 남지 않게 DB 제약으로 막는다(4.1).
- 이메일 삭제 시점: 취소는 즉시, 만료는 만료 시각의 발송 크론(약 1시간 안), **미확인은 24시간이 지난 뒤 다음 발송 크론(최대 하루 더)**.
- Preview에는 `MAIL_*`를 넣지 않는다 → 예약 API가 503을 돌려줘 실제 메일이 나가지 않는다. Preview는 운영 Supabase에 연결하지 않는다(절대 규칙 9).

---

## 9. 환경 변수

| 변수 | 값 | 비밀 |
| --- | --- | --- |
| `MAIL_SMTP_HOST` | `smtp.gmail.com` | |
| `MAIL_SMTP_PORT` | `465` | |
| `MAIL_SMTP_USERNAME` | 서비스 Gmail 주소 | |
| `MAIL_SMTP_PASSWORD` | 앱 비밀번호 16자리 | ✅ |
| `MAIL_FROM` | `리트리버 <서비스 Gmail 주소>` | |
| `PUBLIC_BASE_URL` | 로컬 `http://127.0.0.1:8000`, 운영은 배포 주소 | |
| `ALERT_SECRET` | 무작위 문자열(`secrets.token_urlsafe(32)`) — 링크 토큰과 IP 해시에 쓴다 | ✅ |
| `ALERT_DAILY_SEND_LIMIT` | 선택. 기본 450 | |

`.env.example`·README·CI `env`(더미 값)를 함께 갱신한다. 실제 값은 로컬 `sangsang/.env`와 Vercel에만 둔다.

---

## 10. 테스트

### 10.1 원칙 (`AGENTS.md` 7장)

- 외부 호출 없음: 가짜 발송기(보낸 메일 기록, 오류 흉내)·가짜 저장소. 유사 판정은 가짜 판정기.
- 막히는 경우는 되는 경우와 짝으로. 0건은 0건으로. 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다.
- 위치: `tests/alerts/`(새 영역), `tests/web/`, `tests/database/test_schema_migrations.py`, `tests/deployment/test_vercel_config.py`.
- `test_vercel_config.py`의 크론 기대값은 2개 → 3개로 바꾼다. 11-06 팀 결정으로 사양이 바뀐 것이며 PR에 이유를 적는다.

### 10.2 확인 목록

**예약 API** — 정상 → 202·확인 메일 1통·`pending` ↔ 동의 안 함 → 400·메일 0통 / 검색어 30자 → 202 ↔ 31자·빈 값 → 400 / 반경 3000 → 202 ↔ 2000 → 400 / 이메일 3번째 → 202 ↔ 4번째 → 429 / IP 5번째 → 202 ↔ 6번째 → 429 / 메일 본문에 토큰 있음 ↔ DB에 토큰 없음 / 발송 실패 → 502·남는 예약 0건 / 450통 도달 → 429·남는 예약 0건 / 메일 설정 없음 → 503

**링크 API** — 24시간 안 확인 → `active` ↔ 24시간 뒤 → 거절 / 연장 3회 → 매번 +7일 ↔ 4회째 → 거절 / 만료 뒤 연장 → 거절 / 취소 → 이메일 NULL·대기 메일 0건 / 틀린 토큰 → 400 / **요약 GET 뒤 상태 그대로**

**매칭** — 반경 안 새 물품 → 담김 ↔ 반경 밖 → 0건 / 예약 이후 물품 → 담김 ↔ 이전 물품 → 0건 / 매칭된 물품 → 담김 ↔ `storage_location_id` NULL → 0건 / 첫 실행 알림 ↔ 같은 물품 재실행 → 0건 / partner·police 물품 → 대기 알림 메일 1통 / 만료된 예약 → 0건 / 수집 성공·새 물품 있음 → 실행 ↔ 수집 실패 또는 새 물품 0건 → 건너뜀(DB 호출 0회) / 매칭 예외 → 수집 응답 코드·`ingestion_runs` 그대로 / 시간 예산 초과 → 멈춤, 다음 실행에서 이어 처리 / 판정기 없음 → `skipped`·커서 그대로 / 판정 불통과 물품 → 커서 이동으로 다음 날 후보에서 빠짐

**발송 크론** — Bearer 맞음 → 실행 ↔ 없음 → 401·메일 0통 / 24시간 449통 → 1통 발송·나머지 `pending` ↔ 24시간 지난 기록은 안 셈 / 확인 → 알림 → 만료 안내 순서 / 물품 5개 → 그대로 ↔ 6개 → 5개 + "그 밖에 1개" / 5xx → 그 메일만 `failed`·다음 메일 발송 ↔ `550 5.4.5` → 즉시 멈춤 / 4xx 2회 → 재시도 ↔ 3회 → `failed` / 10분 방치 `sending` → `pending` / 직전 취소 → 0통 / 만료 36시간 이하 → 안내 1통 ↔ 같은 만료일 재실행 → 0통, 연장 뒤 새 만료일로 다시 / 만료 → 이메일 NULL ↔ 미확인 24시간 → 이메일 NULL·행 유지 / 만료일 아침 매칭분 → 정리 전에 발송 / 알림 메일에 "유사한 물품이 새로 등록됐습니다" / 검색어의 `<script>` → escape됨 / 오류 기록 있음 ↔ 그 기록에 앱 비밀번호 없음

**DB·설정** — 새 테이블 RLS·권한 회수·`service_role` 전용 / DB 함수 `execute`는 `service_role`만 / 유일 조건 3종·(예약, 물품) PK / 끝난 예약 이메일 NULL 제약 / `ST_DWithin(..., false)` / `vercel.json` 크론 3개 / `.env.example`에 새 변수

### 10.3 수동 확인 (CI 밖)

1. **DB 함수 실제 동작** — 운영 적용 전에 검증 SQL(기존 `tests/database/verify_detail_mapping.sql` 방식)로 반경 경계·신규 조건·커서·발송 한도를 확인한다.
2. **Gmail 발송(배포 전)** — DB 없이 한 통만 보내는 명령(`python -m retriever_lost_found.alerts.send_test --to <주소>`). 발신: 서비스 Gmail, 수신: 개인 **Gmail 1곳 + 네이버 메일 1곳**. 받은편지함/스팸함(Gmail 프로모션 탭 포함) 확인. 받는 주소는 인자로만 넘긴다. 실제 메일을 보내므로 실행 전에 확인을 받는다.
3. **전체 흐름(배포 후)** — 본인 주소로 실제 예약 1건 → 확인 → 11시대 알림 → 취소. 남는 행 1건은 테스트였다고 `docs/measurements.md`에 적는다.

---

## 11. 측정과 확장

- 첫 운영 실행 뒤 `docs/measurements.md`에 기록: 예약 1건 매칭 시간, 메일 1통 발송 시간, 실행당 발송 수.
- 확장 순서(코드 경계는 지금 만들어 둔다):

| 시점 | 바꾸는 것 | 그대로인 것 |
| --- | --- | --- |
| 450통이 260초 안에 다 안 나감 | 보조 발송 크론 추가(예: 13시대) | 모든 코드 |
| 도메인 구입 | Gmail → 전용 발송 서비스(환경 변수 + `EmailSender` 구현 하나) | 매칭·대기열 |
| 예약이 많아짐 | 매칭을 예약 묶음으로 나눠 대기열 서비스로 분산 | 판정·발송 |
| ⑥이 DB 판정 | `find_new_similar_items` 속 교체 | 바깥 전체 |

---

## 12. 구현 PR에서 함께 고칠 문서

- `.env.example` · README(변수 설명, 앱 비밀번호 발급) · `.github/workflows/ci.yml` `env`
- `vercel.json` 크론 추가
- `docs/PRD.md` 부록의 "Vercel Cron 2개" → 3개
- `AGENTS.md` 8장 엔드포인트 목록에 `/api/alerts*`, `/api/cron/alert-send`
- `docs/team-roles.md` ③의 "결정 필요" 표(발신 수단·연장·취소·개인정보·발송 상한)를 결정됨으로 옮기기
- `docs/measurements.md`(첫 운영 뒤)

---

## 13. 합의가 남은 것

| 항목 | 상대 |
| --- | --- |
| 429 코드 추가, 응답 필드 이름 | 강지현 |
| 링크 페이지 경로, `/alerts/*`에서 `index.html`을 내보내는 서버 변경의 담당 | 강지현 |
| ⑥ 결론 시점(이번 스프린트 안 발송 가능 여부가 달림) | 김성현 |

---

## 14. 구현 계획에서 정한 세부 (2026-11-06)

구현 계획(`2026-11-06-email-scheduler-plan.md`)을 쓰면서 앞 절보다 구체적으로 정한 것이다. 앞 절과 다르면 이 절을 따른다.

| 항목 | 정한 것 | 이유 |
| --- | --- | --- |
| 예약 위치 | 4.1의 `position` geography 컬럼 대신 `latitude`·`longitude` 숫자 컬럼. 비교할 때 DB 함수에서 geography로 만든다 | REST가 geography 값을 읽기 쉬운 모양으로 돌려주지 않는다 |
| 인덱스 추가 | `found_items (item_source_code, first_seen_at)`, `alert_notified_items (found_item_id)` | 예약마다 반경 안 물품 전체를 훑지 않게 하고, 보존기간 삭제가 cascade로 이 표를 찾을 때 느려지지 않게 한다 |
| DB 함수 | 4.6의 7개에 `confirm_alert_reservation`·`extend_alert_reservation`·`alert_match_queue`·`alert_message_payload`·`finish_alert_message`를 더해 12개 | 24시간·연장 조건과 발송 시각을 DB 시계 하나로 판단한다 |
| `alert_runs` 상태 | `skipped` 없음(`running`·`succeeded`·`partial`·`failed`) | 건너뛸 때는 DB를 부르지 않는다(6.1) |
| 일시 오류 재시도 | 같은 실행에서 다시 보내지 않고 다음 실행에서 보낸다. 세 번째 시도에서 실패 | 같은 실행에서 바로 다시 보내면 같은 오류가 반복된다 |
| 링크 API 응답 | 업무 결과는 200 + `result` 코드(`confirmed`·`already_active`·`link_expired`·`ended`·`extended`·`limit_reached`·`cancelled`·`already_ended`)와 한국어 `message`. 토큰이 틀릴 때만 400 | 프런트가 문구를 해석하지 않고 상태를 나눌 수 있다 |
| 좌표 범위 | 위도 33.0~38.7, 경도 124.5~132.0(대한민국, 독도 포함)만 받는다 | 서비스 대상 밖 예약을 막는다 |
| 메일 설정 오류 | 값이 잘못되면(예: 포트) 지도 서비스는 그대로 두고 알림 예약만 끈다. 서버 로그에 경고를 남긴다 | 메일 설정 하나로 사이트 전체가 멈추지 않게 한다 |
| 입력 길이 | 검색어 1~30자(PRD 3.3), 장소 이름 1~100자 | |
