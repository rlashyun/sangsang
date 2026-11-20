# Supabase 변경 파일

`migrations/`는 운영 DB에 적용한 변경을 버전 순서대로 보관한다.
새 변경은 새 버전의 SQL 파일로 추가한다. 이미 적용한 파일의 SQL을 고쳐서 운영을 변경하지 않는다.

## GitHub 자동 배포

- Working directory: `.` (`supabase/`의 상위 디렉터리)
- Production branch name: `main`
- Automatic branching: 끔. PR별 유료 Supabase 테스트 환경을 만들지 않는다.
- Deploy to production: PR #53을 `main`에 병합하고 최신 `main`의 파일과 운영 적용 이력을 다시 대조한 뒤 사용자가 활성화한다.

이 값들은 연동 시 사용할 설정이며, 이번 PR에서 Supabase/Vercel 설정을 변경한 것은 아니다.
연동 후에는 새 SQL을 PR로 검증해 `main`에 병합하고 Supabase 적용 결과를 확인한다.
Vercel 앱 배포와 DB 적용은 별개다. 앱도 함께 바뀌면 기존 앱과의 호환성과 적용 순서를 PR에 적는다.
이미 적용한 SQL의 수정·삭제·재실행이나 이력 행 변경으로 운영을 되돌리지 않고 새 마이그레이션으로 복구한다.

설정과 미적용 SQL 실행 방식의 근거: [Supabase GitHub integration](https://supabase.com/docs/guides/deployment/branching/github-integration).

2026-11-16 운영 기준 정리 내용과 검증 범위는 `../docs/measurements.md`의
"운영 기준 마이그레이션 이력 정렬" 절에 기록했다. 복원한 과거 SQL은 운영에 다시 실행하지 않는다.
마이그레이션 이력 정렬만으로 현재 전체 스키마나 새 DB에서의 재생성이 검증된 것은 아니다.
새 DB 전체 재생성 검증 전에는 Automatic branching을 켜지 않는다.

`maintenance/`, `repairs/`, `rollbacks/`는 별도 수동 작업 자료다.
자동 배포를 위해 이 디렉터리의 SQL을 통째로 실행하거나 `migrations/`로 옮기지 않는다.

## 대화형 검색 공용 세션

`20261120150000_agent_search_sessions.sql`은 `agent_search_sessions`와 `agent_session_command`를 추가하며 기존 검색·수집 객체를 바꾸지 않는다. 새 테이블은 RLS 및 `service_role` 전용이다. `pg_cron`을 활성화하고 매분 만료 세션과 이 작업 자신의 오래된 실행 기록만 삭제하는 `agent-search-session-expiry`를 등록한다. 예약 작업 설치 방식은 [공식 Cron 문서](https://supabase.com/docs/guides/cron/install)를 따른다.

승인 후 SQL 적용 → 권한·RPC·만료 정리 작업 확인 → 앱 배포 순서다. 2026-11-20 사용자 승인으로 운영 SQL을 적용했다. 권한·이력 대조 및 앱 병합·배포·후속 검증 상태는 `../docs/measurements.md`의 운영 기록을 따른다. `tests/database/verify_agent_sessions.sql`은 격리 DB에서 실행한다. 로컬 검증에서 예약 작업 엔진을 실제 실행한 것으로 간주하지 않는다. 앱만 이전 버전으로 돌릴 때 새 객체는 남겨 만료 정리를 계속한다. DB 객체까지 제거하려면 별도 새 마이그레이션에서 `cron.unschedule('agent-search-session-expiry')`, 함수·테이블 삭제를 수행한다. 기존 SQL·운영 이력을 수정하거나 `pg_cron` 확장 전체를 삭제하지 않는다. 상세 계약과 기존 메모리 대화 이관 한계는 [대화형 검색 문서](../docs/agent-search.md)를 따른다.
