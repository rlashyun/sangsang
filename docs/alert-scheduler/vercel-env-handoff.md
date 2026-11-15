# ③ 스케줄러 — Vercel 운영 환경 변수 핸드오프

> 권희준 → 김성현. 알림 메일(③ 스케줄러)을 운영에서 보내려면 Vercel **Production** 환경에 환경 변수 7개가 필요하다.
> 코드는 이 브랜치(`feat-alert-scheduler`, PR #49)에 있다. 설계는 같은 폴더의 `2026-11-06-email-scheduler-design.md`다.
> **이 문서에는 비밀값과 서비스 Gmail 주소를 적지 않는다**(공개 저장소). 둘은 권희준이 따로 보낸다. `ALERT_SECRET`은 받는 사람이 직접 만든다.

---

## 1. 순서

**운영 DB 마이그레이션 적용 → 환경 변수 등록 → PR #49 병합(배포)**

- **운영 DB 마이그레이션은 둘 다 적용했다**(2026-11-15, `docs/measurements.md`). 환경 변수만 넣으면 된다.
  - `20261115090000_create_alert_scheduler.sql`
  - `20261115130000_alert_reservation_places.sql`(여러 장소)
- 마이그레이션 없이 코드가 배포되면 11시 발송 크론이 매일 500 오류를 내고 예약 API도 실패한다. 그래서 이 순서를 지킨다.

---

## 2. Vercel에 넣을 값

Vercel 대시보드 → 프로젝트 → **Settings → Environment Variables**에서 추가한다. **환경은 Production만 체크한다.**

| 변수 | 값 | 비고 |
| --- | --- | --- |
| `MAIL_SMTP_HOST` | `smtp.gmail.com` | 그대로 |
| `MAIL_SMTP_PORT` | `465` | 그대로 |
| `MAIL_SMTP_USERNAME` | 서비스 Gmail 주소 | **권희준이 따로 보낸다**(아래 3장). 알림 메일을 보내는 서비스 전용 계정 |
| `MAIL_FROM` | `리트리버 <서비스 Gmail 주소>` | 받는 사람에게 "리트리버"로 보인다. `<>` 안 주소는 위 `MAIL_SMTP_USERNAME`과 같아야 한다 |
| `PUBLIC_BASE_URL` | `https://sangsang-retriever-mvp.vercel.app` | 지금 운영 배포 주소(2026-11-15 응답 200 확인). 끝에 `/`를 붙이지 않는다. 메일 속 링크의 앞부분이 된다. **Settings → Domains**에 다른 대표 도메인이 있으면 그 주소를 쓴다 |
| `MAIL_SMTP_PASSWORD` | 앱 비밀번호 16자리, 공백 없이 | **권희준이 따로 보낸다**(아래 3장) |
| `ALERT_SECRET` | 무작위 문자열(43자) | **김성현이 직접 만든다**(아래 3장) |

**넣지 않는 변수**

- `MAIL_PROVIDER` — 비우면 Gmail(SMTP)로 보낸다.
- `ALERT_DAILY_SEND_LIMIT` — 비우면 하루 450통.
- `RESEND_API_KEY` — Resend는 쓰지 않는다(도메인을 산 뒤를 위한 예비).
- `SUPABASE_URL`·`SUPABASE_SECRET_KEY`·`CRON_SECRET` — 이미 있다. 새 발송 크론(`/api/cron/alert-send`)도 같은 `CRON_SECRET`을 쓴다.

---

## 3. 따로 주고받는 값

### 서비스 Gmail 주소와 `MAIL_SMTP_PASSWORD` — 권희준이 보낸다

- 서비스 Gmail 계정에서 운영용 앱 비밀번호(`retriever-vercel`)를 새로 만든다. 주소와 함께 **카카오톡 1:1**로 따로 보낸다.
- Vercel에 넣은 뒤에는 **받은 메시지를 지운다.** 이 값이 있으면 서비스 Gmail 계정으로 메일을 보내고 읽을 수 있다.

### `ALERT_SECRET` — 김성현이 직접 만든다

저장소 폴더에서 아래 명령을 실행한다. 나온 한 줄을 그대로 Vercel에 넣고, **누구에게도 보내지 않는다.**

```bash
uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- 메일 속 확인·연장·취소 링크를 만드는 데 쓰는 값이다. 알려지면 링크를 위조할 수 있다.
- **한 번 넣은 뒤에는 바꾸지 않는다.** 바꾸면 이미 보낸 메일의 링크가 모두 무효가 된다.
- 로컬 `.env`의 값과 다른 값을 쓴다.

---

## 4. 주의할 점

1. **Preview·Development 환경에는 `MAIL_*`를 넣지 않는다.** 메일 설정이 없으면 예약 API가 503을 돌려준다. 그래서 테스트 배포에서는 실제 메일이 나가지 않는다.
2. **환경 변수는 다음 배포부터 적용된다.** PR 병합 전에 넣으면 병합 배포 때 같이 반영된다. 이미 배포한 뒤에 넣었다면 **Redeploy**를 한 번 한다.
3. 하루 발송 상한은 450통이다. Gmail 개인 계정의 하루 한도(약 500통)보다 낮게 잡았다.

---

## 5. 넣은 뒤 확인

- **배포 다음 날:** Vercel → **Logs**에서 `/api/cron/alert-send`가 KST 11시대에 **200**으로 끝났는지 본다. 예약이 없어도 빈 실행으로 200이 나와야 정상이다.
  - `503 "알림 메일 설정이 없습니다."` → Production에 `MAIL_*`가 빠졌다.
  - `500` → 권희준에게 알린다. 원인은 DB 실행 기록(`alert_runs.error_message`, 비밀값은 가려져 있다)에서 본다.
- 끝나면 **넣은 날짜와 Redeploy 여부**를 권희준에게 알린다.
