# 일회성 검색 자료 재배치

대상은 새 파생 테이블 `found_item_search_vectors`, `found_item_search_documents`뿐이다.
원본 `found_items`, `found_item_embeddings_bge_m3`의 행·벡터는 바꾸지 않는다.
초기 MCP 요청 오류를 rollback으로 판단했으나, 이후 원격 이력에서 벡터 CLUSTER migration `20261111111021`의 적용을 확인했다.

실행 당시 사용자 승인 절차:

1. 기존에 없던 `pg_cron` 확장을 일시 활성화하는 migration을 적용한다.
2. DB 시각 기준 다음 분에 한 번 실행되는 날짜/시간 cron 작업으로 `cluster_search_projection.sql`의
   vector 재배치와 document 재배치를 실행한다. command의 statement_timeout은 5분이며
   외부 네트워크나 임베딩 API를 호출하지 않는다. 작업 상태를 현재 작업에서 추적한다.
3. 성공/실패가 확인되면 작업 예약을 해제한다. 다른 작업이 없음을 확인한 후 이번에 활성화한
   `pg_cron` 확장도 후속 migration으로 제거한다. 원본과 실제 검색 테이블은 보존한다.
4. 적용 버전·작업 상태·정리 결과를 복구 기록에 남긴다.

자동 승인 검토는 최초 1단계 활성화를 거절했다. 이후 사용자가 “일회성 실행과 이후 제거 승인”을
명시적으로 답했고, `20261111120000_enable_oneoff_search_maintenance`를 적용했다.
기존 cron 확장·작업은 없었다. 새 작업 ID 1은 2026-11-11 12:01 UTC에 실행을 시작했다.
작업은 12:03:31.184646 UTC에 성공했다. 예약 해제 후 남은 작업 0을 확인하고 `20261111120531`로 pg_cron까지 제거했다. 이 문서는 다른 작업의 예약/확장 유지 승인이 아니다.
