# Supabase 변경 파일

`migrations/`는 운영 DB에 적용한 변경을 버전 순서대로 보관한다.
새 변경은 새 버전의 SQL 파일로 추가한다. 이미 적용한 파일의 SQL을 고쳐서 운영을 변경하지 않는다.

## GitHub 자동 배포

- Working directory: `.` (`supabase/`의 상위 디렉터리)
- Production branch name: `main`
- Automatic branching: 끔. PR별 유료 Supabase 테스트 환경을 만들지 않는다.
- Deploy to production: 저장소 파일과 운영 적용 이력을 대조한 뒤 활성화한다.

2026-11-16 운영 기준 정리 내용과 검증 범위는 `../docs/measurements.md`의
"운영 기준 마이그레이션 이력 정렬" 절에 기록했다. 복원한 과거 SQL은 운영에 다시 실행하지 않는다.
마이그레이션 이력 정렬만으로 현재 전체 스키마나 새 DB에서의 재생성이 검증된 것은 아니다.

`maintenance/`, `repairs/`, `rollbacks/`는 별도 수동 작업 자료다.
자동 배포를 위해 이 디렉터리의 SQL을 통째로 실행하거나 `migrations/`로 옮기지 않는다.
