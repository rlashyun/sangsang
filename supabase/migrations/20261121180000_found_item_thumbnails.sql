-- PRD 3.2-2 · 5 — 축소본은 검색 원문 및 검색 인덱스와 분리한다.
-- 작업 기록은 물품 삭제 후에도 남아 Storage 정리가 끝나야 사라진다.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('found-item-thumbnails', 'found-item-thumbnails', true, 1048576, array['image/jpeg'])
on conflict (id) do nothing;
do $$
begin
  if not exists (select 1 from storage.buckets where id = 'found-item-thumbnails'
                 and public and file_size_limit = 1048576 and allowed_mime_types = array['image/jpeg']) then
    raise exception 'Existing thumbnail bucket has incompatible settings';
  end if;
end $$;
create table public.found_item_thumbnails (
    item_id bigint not null,
    source_url text not null,
    object_key text not null,
    status text not null default 'pending' check (status in ('pending', 'ready')),
    deleted_at timestamptz,
    lease_token uuid,
    lease_until timestamptz,
    retry_after timestamptz not null default now(),
    primary key (item_id, object_key)
);
alter table public.found_item_thumbnails enable row level security;
revoke all on public.found_item_thumbnails from public, anon, authenticated;
grant select, insert, update, delete on public.found_item_thumbnails to service_role;
create index found_item_thumbnails_pending_idx on public.found_item_thumbnails (retry_after, item_id)
    where status = 'pending' or deleted_at is not null;
create table public.thumbnail_backfill_progress (
    singleton boolean primary key default true check (singleton),
    last_id bigint not null default 0,
    completed boolean not null default false,
    orphan_item_id bigint not null default 0,
    orphan_key text not null default '',
    orphan_completed boolean not null default false,
    next_scan_at timestamptz not null default now()
);
insert into public.thumbnail_backfill_progress default values;
alter table public.thumbnail_backfill_progress enable row level security;
revoke all on public.thumbnail_backfill_progress from public, anon, authenticated;
grant select, update on public.thumbnail_backfill_progress to service_role;
create table public.thumbnail_worker_guard (
    singleton boolean primary key default true check (singleton),
    enabled boolean not null default false,
    lease_token uuid,
    lease_until timestamptz
);
insert into public.thumbnail_worker_guard default values;
alter table public.thumbnail_worker_guard enable row level security;
revoke all on public.thumbnail_worker_guard from public, anon, authenticated;
grant select, update on public.thumbnail_worker_guard to service_role;

create function public.thumbnail_worker_claim() returns uuid
language sql security invoker set search_path = '' as $$
  update public.thumbnail_worker_guard set lease_token = gen_random_uuid(), lease_until = now() + interval '7 minutes'
  where singleton and enabled and (lease_until is null or lease_until < now()) returning lease_token
$$;
create function public.thumbnail_worker_release(p_token uuid) returns void
language sql security invoker set search_path = '' as $$
  update public.thumbnail_worker_guard set lease_token = null, lease_until = null
  where singleton and lease_token = p_token
$$;

create function public.thumbnail_enqueue() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    update public.found_item_thumbnails set deleted_at = now(), retry_after = now()
      where item_id = old.id;
    return old;
  end if;
  if new.image_url is not null and btrim(new.image_url) <> ''
     and new.image_url !~ '/img02_no_img\.gif([?#]|$)' then
    insert into public.found_item_thumbnails(item_id, source_url, object_key)
    values (new.id, new.image_url,
      new.id::text || '/' || encode(extensions.digest(new.image_url, 'sha256'), 'hex') || '.jpg')
    on conflict do nothing;
  end if;
  return new;
exception when others then
  -- PRD 5 — 기록 실패는 수집을 막지 않는다. 주기적 전체 대조가 누락을 복구한다.
  raise warning 'thumbnail enqueue deferred (%)', sqlstate;
  if tg_op = 'DELETE' then return old; end if;
  return new;
end $$;

create trigger found_items_thumbnail_insert after insert on public.found_items
  for each row execute function public.thumbnail_enqueue();
create trigger found_items_thumbnail_update after update of image_url on public.found_items
  for each row when (old.image_url is distinct from new.image_url) execute function public.thumbnail_enqueue();
create trigger found_items_thumbnail_delete after delete on public.found_items
  for each row execute function public.thumbnail_enqueue();

-- 호출자가 커서를 보관해 작은 범위씩 기존 사진을 등록한다.
create function public.thumbnail_backfill(p_after bigint, p_limit integer)
returns bigint language plpgsql security invoker set search_path = '' as $$
declare last_id bigint;
begin
  with batch as materialized (
    select id, image_url from public.found_items where id > p_after
    order by id limit greatest(1, least(p_limit, 500)) for key share
  ), added as (
    insert into public.found_item_thumbnails(item_id, source_url, object_key)
    select id, image_url, id::text || '/' || encode(extensions.digest(image_url, 'sha256'), 'hex') || '.jpg'
    from batch where image_url is not null and btrim(image_url) <> ''
      and image_url !~ '/img02_no_img\.gif([?#]|$)' on conflict do nothing returning item_id
  ) select coalesce(max(id), p_after) into last_id from batch;
  return last_id;
end $$;

create function public.thumbnail_backfill_next(p_limit integer)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare
  progress public.thumbnail_backfill_progress%rowtype;
  next_id bigint;
  orphan_id bigint;
  orphan_key_value text;
begin
  select * into progress from public.thumbnail_backfill_progress where singleton for update;
  if progress.completed and progress.orphan_completed then
    if progress.next_scan_at > now() then return true; end if;
    progress.last_id := 0;
    progress.completed := false;
    progress.orphan_item_id := 0;
    progress.orphan_key := '';
    progress.orphan_completed := false;
  end if;
  if not progress.completed then
    next_id := public.thumbnail_backfill(progress.last_id, p_limit);
    progress.completed := next_id = progress.last_id;
    progress.last_id := next_id;
  end if;
  if not progress.orphan_completed then
    with batch as materialized (
      select item_id, object_key from public.found_item_thumbnails
      where (item_id, object_key) > (progress.orphan_item_id, progress.orphan_key)
      order by item_id, object_key limit greatest(1, least(p_limit, 500))
    ), removed as (
      update public.found_item_thumbnails t set deleted_at = now(), retry_after = now()
      from batch b where t.item_id = b.item_id and t.object_key = b.object_key
        and t.deleted_at is null and not exists (select 1 from public.found_items f where f.id = t.item_id)
      returning t.item_id
    ) select item_id, object_key into orphan_id, orphan_key_value from batch order by item_id desc, object_key desc limit 1;
    if orphan_id is null then
      progress.orphan_completed := true;
    else
      progress.orphan_item_id := orphan_id;
      progress.orphan_key := orphan_key_value;
    end if;
  end if;
  if progress.completed and progress.orphan_completed then
    progress.next_scan_at := now() + interval '1 day';
  end if;
  update public.thumbnail_backfill_progress set
    last_id = progress.last_id, completed = progress.completed,
    orphan_item_id = progress.orphan_item_id, orphan_key = progress.orphan_key,
    orphan_completed = progress.orphan_completed, next_scan_at = progress.next_scan_at
  where singleton;
  return progress.completed and progress.orphan_completed;
end $$;

-- 하나씩 임대해 중복 업로드와 삭제 경합을 방지한다. 작업자가 중단돼도 임대 만료 후 재개한다.
create function public.thumbnail_claim()
returns setof public.found_item_thumbnails language sql security invoker set search_path = '' as $$
  update public.found_item_thumbnails t set lease_token = gen_random_uuid(), lease_until = now() + interval '10 minutes'
  where (t.item_id, t.object_key) = (
    select q.item_id, q.object_key from public.found_item_thumbnails q
    where (q.lease_until is null or q.lease_until < now()) and q.retry_after <= now()
      and (q.deleted_at is not null or (q.status = 'pending' and exists (
        select 1 from public.found_items f where f.id = q.item_id and f.image_url = q.source_url)))
    order by q.retry_after, q.item_id for update skip locked limit 1
  ) returning t.*
$$;

create function public.thumbnail_finish(p_item_id bigint, p_key text, p_token uuid, p_success boolean)
returns void language plpgsql security invoker set search_path = '' as $$
begin
  -- 삭제 작업은 삭제 사실이 유지되는 상태에서 성공했을 때에만 기록까지 제거한다.
  if p_success then
    delete from public.found_item_thumbnails where item_id = p_item_id and object_key = p_key
      and lease_token = p_token and deleted_at is not null and status = 'ready';
    if found then return; end if;
  end if;
  update public.found_item_thumbnails set
    status = case when p_success then 'ready' else status end,
    lease_token = null, lease_until = null,
    retry_after = case when p_success then now() else now() + interval '1 hour' end
  where item_id = p_item_id and object_key = p_key and lease_token = p_token;
end $$;

revoke all on function public.thumbnail_worker_claim(), public.thumbnail_worker_release(uuid),
 public.thumbnail_enqueue(), public.thumbnail_backfill(bigint, integer), public.thumbnail_backfill_next(integer),
 public.thumbnail_claim(), public.thumbnail_finish(bigint, text, uuid, boolean) from public, anon, authenticated;
grant execute on function public.thumbnail_worker_claim(), public.thumbnail_worker_release(uuid),
 public.thumbnail_enqueue(), public.thumbnail_backfill(bigint, integer), public.thumbnail_backfill_next(integer),
 public.thumbnail_claim(), public.thumbnail_finish(bigint, text, uuid, boolean) to service_role;
