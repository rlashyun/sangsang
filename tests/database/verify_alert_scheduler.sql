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
    v_claimed integer;
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
        'verify@example.com', '에어팟', '서울역', 37.5547, 126.9707, 1000,
        'verify-ip', 'verify-nonce', now() + interval '7 days'
    );
    if v_created ->> 'status' <> 'created' then
        raise exception '예약 생성 실패: %', v_created;
    end if;
    v_reservation := (v_created ->> 'reservation_id')::bigint;
    if public.confirm_alert_reservation(v_reservation) <> 'confirmed' then
        raise exception '확인 실패';
    end if;

    -- 같은 트랜잭션 안에서는 now()가 같아서 커서를 1분 앞당겨 "예약 이후 물품"을 만든다.
    update public.alert_match_cursors
    set checked_until = now() - interval '1 minute'
    where reservation_id = v_reservation;

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

    -- 반경 안 + 매칭된 물품 1건만 후보가 된다.
    v_payload := public.alert_candidate_items(v_reservation, 'partner_api');
    if jsonb_array_length(v_payload -> 'items') <> 1
       or (v_payload -> 'items' -> 0 ->> 'id')::bigint <> v_item_inside then
        raise exception '반경·매칭 조건이 틀렸습니다: %', v_payload;
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
       or v_payload -> 'reservation' ->> 'email' <> 'verify@example.com' then
        raise exception '메일 내용 조회 실패: %', v_payload;
    end if;

    -- 만료 안내는 만료일마다 한 번, 만료되면 이메일이 지워진다.
    v_created := public.create_alert_reservation(
        'expiring@example.com', '지갑', '서울역', 37.5547, 126.9707, 1000,
        null, 'verify-nonce-2', now() + interval '24 hours'
    );
    v_expiring := (v_created ->> 'reservation_id')::bigint;
    perform public.confirm_alert_reservation(v_expiring);
    if public.enqueue_alert_expiry_notices() <> 1 then
        raise exception '만료 안내가 들어가지 않았습니다';
    end if;
    if public.enqueue_alert_expiry_notices() <> 0 then
        raise exception '같은 만료일 안내가 두 번 들어갔습니다';
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
            'verify' || v_claimed || '@example.com', '에어팟', '서울역', 37.5547, 126.9707, 1000,
            'verify-ip', 'verify-nonce-ip-' || v_claimed, now() + interval '7 days'
        );
    end loop;
    v_created := public.create_alert_reservation(
        'verify6@example.com', '에어팟', '서울역', 37.5547, 126.9707, 1000,
        'verify-ip', 'verify-nonce-ip-6', now() + interval '7 days'
    );
    if v_created ->> 'status' <> 'ip_limit' then
        raise exception '같은 IP 6번째 예약이 막히지 않았습니다: %', v_created;
    end if;

    raise notice '알림 스케줄러 DB 검증 통과';
end;
$$;

rollback;
