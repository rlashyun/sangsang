-- 설계 10.3-1 — 알림 스케줄러 DB 함수 수동 검증.
-- 운영 DB가 아닌 곳(로컬 `supabase start` 후 `supabase db reset`)에서 실행한다.
-- 마지막에 rollback하므로 데이터가 남지 않는다.
-- 실행: psql "$LOCAL_DB_URL" -v ON_ERROR_STOP=1 -f tests/database/verify_alert_scheduler.sql
begin;

do $$
declare
    v_location_source text;
    v_category smallint;
    v_inside bigint;
    v_outside bigint;
    v_created jsonb;
    v_reservation bigint;
    v_expiring bigint;
    v_message bigint;
    v_payload jsonb;
    v_item_inside bigint;
    v_item_outside bigint;
    v_multi bigint;
    v_claimed integer;
    v_late bigint;
    v_discard bigint;
begin
    select code into v_location_source from public.location_sources order by code limit 1;
    select id into v_category from public.item_categories order by id limit 1;

    -- 서울역(37.5547, 126.9707)에서 북쪽 약 990m(반경 1km 안)와 약 2km(밖)
    insert into public.storage_locations (location_source_code, source_key, name, normalized_name, position)
    values (
        v_location_source, 'verify-inside', '검증 안쪽', '검증안쪽',
        extensions.st_setsrid(extensions.st_makepoint(126.9707, 37.5636), 4326)::extensions.geography
    )
    returning id into v_inside;
    insert into public.storage_locations (location_source_code, source_key, name, normalized_name, position)
    values (
        v_location_source, 'verify-outside', '검증 바깥', '검증바깥',
        extensions.st_setsrid(extensions.st_makepoint(126.9707, 37.5727), 4326)::extensions.geography
    )
    returning id into v_outside;

    v_created := public.create_alert_reservation(
        'verify@example.com', '에어팟', '[{"place_name": "서울역", "latitude": 37.5547, "longitude": 126.9707}]'::jsonb, 1000,
        'verify-ip', 'verify-nonce', now() + interval '7 days'
    );
    if v_created ->> 'status' <> 'created' then
        raise exception '예약 생성 실패: %', v_created;
    end if;
    v_reservation := (v_created ->> 'reservation_id')::bigint;
    if public.confirm_alert_reservation(v_reservation) <> 'confirmed' then
        raise exception '확인 실패';
    end if;

    -- 같은 트랜잭션 안에서는 now()가 같아서 예약 시각과 커서를 1분 앞당겨 "예약 이후 물품"을 만든다.
    update public.alert_match_cursors
    set checked_until = now() - interval '1 minute'
    where reservation_id = v_reservation;
    update public.alert_reservations set created_at = now() - interval '1 minute' where id = v_reservation;

    insert into public.found_items (
        item_source_code, atc_id, found_sequence, storage_location_id, location_match_status,
        category_id, raw_category_name, item_name, registered_on
    )
    values ('partner_api', 'verify-1', '1', v_inside, 'matched', v_category, '무선이어폰', '에어팟', current_date)
    returning id into v_item_inside;
    insert into public.found_items (
        item_source_code, atc_id, found_sequence, storage_location_id, location_match_status,
        category_id, raw_category_name, item_name, registered_on
    )
    values
        ('partner_api', 'verify-2', '1', v_outside, 'matched', v_category, '무선이어폰', '에어팟', current_date),
        ('partner_api', 'verify-3', '1', null, 'unmatched', v_category, '무선이어폰', '에어팟', current_date);

    -- 반경 안 + 매칭된 물품 1건만 후보가 된다. 판정이 운영 검색과 같은 글을 보도록 검색 투영의
    -- search_text가 함께 온다(found_items_sync_search 트리거가 채운다).
    v_payload := public.alert_candidate_items(v_reservation, 'partner_api');
    if jsonb_array_length(v_payload -> 'items') <> 1
       or (v_payload -> 'items' -> 0 ->> 'id')::bigint <> v_item_inside
       or strpos(v_payload -> 'items' -> 0 ->> 'search_text', '에어팟') = 0 then
        raise exception '반경·매칭 조건이 틀렸습니다: %', v_payload;
    end if;

    -- PRD 4.3-E — 등록일(D)에는 비교하지 않고, 다음날(D+1)부터 비교한다.
    if exists (select 1 from public.alert_match_queue('partner_api') as queued where queued.id = v_reservation) then
        raise exception '등록일에 비교 대상이 됐습니다';
    end if;
    update public.alert_reservations set created_at = now() - interval '1 day' where id = v_reservation;
    if not exists (select 1 from public.alert_match_queue('partner_api') as queued where queued.id = v_reservation) then
        raise exception '다음날에도 비교 대상이 아닙니다';
    end if;

    v_payload := public.record_alert_match(
        v_reservation, 'partner_api', (v_payload ->> 'checked_until')::timestamptz,
        jsonb_build_array(jsonb_build_object('found_item_id', v_item_inside, 'score', 1.0))
    );
    if (v_payload ->> 'items_queued')::integer <> 1 then
        raise exception '알림 물품 기록 실패: %', v_payload;
    end if;
    v_payload := public.record_alert_match(
        v_reservation, 'partner_api', now(),
        jsonb_build_array(jsonb_build_object('found_item_id', v_item_inside, 'score', 1.0))
    );
    if (v_payload ->> 'items_queued')::integer <> 0 then
        raise exception '같은 물품이 두 번 기록됐습니다';
    end if;
    if jsonb_array_length(public.alert_candidate_items(v_reservation, 'partner_api') -> 'items') <> 0 then
        raise exception '커서가 움직이지 않았습니다';
    end if;

    -- 리뷰 2026-11-15 — 수집 때 unmatched였다가 나중에 기관이 연결된 물품도 후보가 된다.
    -- 트리거가 검색 투영을 새로 넣고 visible_since가 그 시각이 된다(트랜잭션 안이라 직접 앞당긴다).
    update public.alert_match_cursors set checked_until = now() - interval '30 seconds'
    where reservation_id = v_reservation;
    select id into v_late from public.found_items where atc_id = 'verify-3';
    update public.found_items set storage_location_id = v_inside, location_match_status = 'matched' where id = v_late;
    if not exists (select 1 from public.found_item_search_documents where item_id = v_late) then
        raise exception '나중에 연결된 물품의 검색 투영이 생기지 않았습니다';
    end if;
    v_payload := public.alert_candidate_items(v_reservation, 'partner_api');
    if jsonb_array_length(v_payload -> 'items') <> 1 or (v_payload -> 'items' -> 0 ->> 'id')::bigint <> v_late then
        raise exception '나중에 연결된 물품이 후보가 되지 않았습니다: %', v_payload;
    end if;
    -- 짝 — 예약 전에 처음 들어온 물품은 나중에 연결돼도 후보가 아니다(first_seen_at은 트리거가 고정하므로
    -- 예약 시각을 물품보다 뒤로 옮겨 같은 상황을 만든다).
    update public.alert_reservations set created_at = now() + interval '1 second' where id = v_reservation;
    if jsonb_array_length(public.alert_candidate_items(v_reservation, 'partner_api') -> 'items') <> 0 then
        raise exception '예약 전 물품이 후보가 됐습니다';
    end if;

    -- 한도 1이면 한 통만 집고(확인 메일이 먼저), 보낸 뒤에는 24시간 동안 더 집지 않는다.
    select count(*) into v_claimed from public.claim_alert_messages(10, 1);
    if v_claimed <> 1 then
        raise exception '한도 1인데 %통을 집었습니다', v_claimed;
    end if;
    select id into v_message from public.alert_messages where status = 'sending' limit 1;
    perform public.finish_alert_message(v_message, 'sent', null);
    select count(*) into v_claimed from public.claim_alert_messages(10, 1);
    if v_claimed <> 0 then
        raise exception '최근 24시간 발송 수를 세지 않았습니다';
    end if;

    select id into v_message
    from public.alert_messages
    where reservation_id = v_reservation and kind = 'match';
    v_payload := public.alert_message_payload(v_message);
    if jsonb_array_length(v_payload -> 'items') <> 1
       or v_payload -> 'reservation' ->> 'email' <> 'verify@example.com'
       or v_payload -> 'reservation' -> 'places' <> '[{"name": "서울역"}]'::jsonb then
        raise exception '메일 내용 조회 실패: %', v_payload;
    end if;

    -- 설계 16장 — 여러 장소: 장소들 각각의 반경 안 기관을 합치고, 물품마다 가장 가까운 장소를 붙인다.
    select id into v_item_outside from public.found_items where atc_id = 'verify-2';
    v_created := public.create_alert_reservation(
        'multi@example.com', '에어팟',
        '[{"place_name": "서울역", "latitude": 37.5547, "longitude": 126.9707},
          {"place_name": "검증 북쪽", "latitude": 37.5727, "longitude": 126.9707}]'::jsonb,
        1000, null, 'verify-nonce-multi', now() + interval '7 days'
    );
    v_multi := (v_created ->> 'reservation_id')::bigint;
    perform public.confirm_alert_reservation(v_multi);
    update public.alert_match_cursors set checked_until = now() - interval '1 minute' where reservation_id = v_multi;
    update public.alert_reservations set created_at = now() - interval '1 minute' where id = v_multi;
    v_payload := public.alert_candidate_items(v_multi, 'partner_api');
    if (select array_agg((entry ->> 'id')::bigint order by (entry ->> 'id')::bigint)
        from jsonb_array_elements(v_payload -> 'items') as entry)
       is distinct from (select array_agg(id order by id) from unnest(array[v_item_inside, v_item_outside, v_late]) as id) then
        raise exception '여러 장소의 반경을 합치지 못했습니다: %', v_payload;
    end if;
    perform public.record_alert_match(
        v_multi, 'partner_api', now(),
        jsonb_build_array(
            jsonb_build_object('found_item_id', v_item_inside, 'score', 1.0),
            jsonb_build_object('found_item_id', v_item_outside, 'score', 0.5)
        )
    );
    select id into v_message
    from public.alert_messages
    where reservation_id = v_multi and kind = 'match';
    v_payload := public.alert_message_payload(v_message);
    if v_payload -> 'reservation' -> 'places' <> '[{"name": "서울역"}, {"name": "검증 북쪽"}]'::jsonb
       or v_payload -> 'items' -> 0 ->> 'near_place_name' <> '서울역'
       or v_payload -> 'items' -> 1 ->> 'near_place_name' <> '검증 북쪽' then
        raise exception '여러 장소 메일 내용이 틀렸습니다: %', v_payload;
    end if;
    begin
        perform public.create_alert_reservation(
            'four@example.com', '에어팟',
            '[{"place_name": "가", "latitude": 37.55, "longitude": 126.97},
              {"place_name": "나", "latitude": 37.56, "longitude": 126.97},
              {"place_name": "다", "latitude": 37.57, "longitude": 126.97},
              {"place_name": "라", "latitude": 37.58, "longitude": 126.97}]'::jsonb,
            1000, null, 'verify-nonce-four', now() + interval '7 days'
        );
        raise exception '장소 4곳이 저장됐습니다';
    exception when raise_exception then
        if sqlerrm = '장소 4곳이 저장됐습니다' then
            raise;
        end if;
    end;
    perform public.cancel_alert_reservation(v_multi);
    if (select count(*) from public.alert_reservation_places where reservation_id = v_multi) <> 2 then
        raise exception '취소한 예약의 장소(통계용)가 지워졌습니다';
    end if;

    -- PRD 4.3-E — 종료 안내는 마지막 날 실행(만료 24시간 안)에서만, 만료일마다 한 번.
    -- 그날 기다리는 알림 메일은 종료 안내로 바뀌어 한 통이 되고, 그 뒤에는 비교하지 않는다.
    v_created := public.create_alert_reservation(
        'expiring@example.com', '지갑', '[{"place_name": "서울역", "latitude": 37.5547, "longitude": 126.9707}]'::jsonb, 1000,
        null, 'verify-nonce-2', now() + interval '37 hours'
    );
    v_expiring := (v_created ->> 'reservation_id')::bigint;
    perform public.confirm_alert_reservation(v_expiring);
    if public.enqueue_alert_expiry_notices() <> 0 then
        raise exception '마지막 날 전에 종료 안내가 들어갔습니다';
    end if;
    update public.alert_reservations set expires_at = now() + interval '13 hours' where id = v_expiring;
    insert into public.alert_messages (reservation_id, kind) values (v_expiring, 'match');
    if public.enqueue_alert_expiry_notices() <> 1 then
        raise exception '종료 안내가 들어가지 않았습니다';
    end if;
    if exists (select 1 from public.alert_messages where reservation_id = v_expiring and kind = 'match')
       or (select count(*) from public.alert_messages where reservation_id = v_expiring and kind = 'expiry_notice') <> 1 then
        raise exception '그날 알림 메일이 종료 안내 한 통으로 합쳐지지 않았습니다';
    end if;
    if public.enqueue_alert_expiry_notices() <> 0 then
        raise exception '같은 만료일 안내가 두 번 들어갔습니다';
    end if;
    update public.alert_reservations set created_at = now() - interval '7 days' where id = v_expiring;
    if exists (select 1 from public.alert_match_queue('partner_api') as queued where queued.id = v_expiring) then
        raise exception '종료 안내 뒤에도 비교 대상입니다';
    end if;
    update public.alert_reservations set expires_at = now() - interval '1 minute' where id = v_expiring;
    v_payload := public.close_ended_alert_reservations();
    if (v_payload ->> 'expired')::integer <> 1
       or exists (select 1 from public.alert_reservations where id = v_expiring and email is not null) then
        raise exception '만료 정리 실패: %', v_payload;
    end if;

    -- 취소하면 이메일이 지워지고 대기 메일이 사라진다.
    if public.cancel_alert_reservation(v_reservation) <> 'cancelled' then
        raise exception '취소 실패';
    end if;
    if exists (select 1 from public.alert_reservations where id = v_reservation and email is not null)
       or exists (select 1 from public.alert_messages where reservation_id = v_reservation and status = 'pending') then
        raise exception '취소 뒤 개인정보나 대기 메일이 남았습니다';
    end if;

    -- 설계 14장 — 취소해도 IP 제한 기록은 24시간 동안 남는다(취소로 제한을 풀 수 없다).
    if (select count(*) from public.alert_request_log where ip_hash = 'verify-ip') <> 1 then
        raise exception '취소가 IP 제한 기록을 지웠습니다';
    end if;
    for v_claimed in 1..4 loop
        perform public.create_alert_reservation(
            'verify' || v_claimed || '@example.com', '에어팟', '[{"place_name": "서울역", "latitude": 37.5547, "longitude": 126.9707}]'::jsonb, 1000,
            'verify-ip', 'verify-nonce-ip-' || v_claimed, now() + interval '7 days'
        );
    end loop;
    v_created := public.create_alert_reservation(
        'verify6@example.com', '에어팟', '[{"place_name": "서울역", "latitude": 37.5547, "longitude": 126.9707}]'::jsonb, 1000,
        'verify-ip', 'verify-nonce-ip-6', now() + interval '7 days'
    );
    if v_created ->> 'status' <> 'ip_limit' then
        raise exception '같은 IP 6번째 예약이 막히지 않았습니다: %', v_created;
    end if;

    -- 리뷰 2026-11-15 — 확인 메일을 못 보내 지운 예약은 IP 기록도 지워 5건 제한을 깎지 않는다.
    v_created := public.create_alert_reservation(
        'discard@example.com', '에어팟', '[{"place_name": "서울역", "latitude": 37.5547, "longitude": 126.9707}]'::jsonb, 1000,
        'discard-ip', 'verify-nonce-discard', now() + interval '7 days'
    );
    v_discard := (v_created ->> 'reservation_id')::bigint;
    perform public.discard_alert_reservation(v_discard, 'discard-ip');
    if exists (select 1 from public.alert_reservations where id = v_discard)
       or exists (select 1 from public.alert_request_log where ip_hash = 'discard-ip') then
        raise exception '지운 예약이나 IP 기록이 남았습니다';
    end if;
    -- 짝 — 확인된 예약은 이 함수로 지워지지 않는다.
    perform public.discard_alert_reservation(v_expiring, null);
    if not exists (select 1 from public.alert_reservations where id = v_expiring) then
        raise exception '끝난 예약이 지워졌습니다';
    end if;

    raise notice '알림 스케줄러 DB 검증 통과';
end;
$$;

rollback;
