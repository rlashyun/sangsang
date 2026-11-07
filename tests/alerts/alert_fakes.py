"""③ 스케줄러 테스트용 설정·가짜 저장소·가짜 발송기. 외부 서비스를 부르지 않는다(AGENTS.md 7장)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from retriever_lost_found.alerts.settings import AlertSettings, MailSettings

# 2026-11-06 15:00 KST
NOW = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)

TEST_SETTINGS = AlertSettings(
    mail=MailSettings(
        host="smtp.example.com",
        port=465,
        username="sender@example.com",
        password="app-password-1234",
        from_address="리트리버 <sender@example.com>",
    ),
    public_base_url="https://retriever.example",
    secret="alert-test-secret",
    daily_send_limit=450,
)


def reservation_row(**overrides: Any) -> dict[str, Any]:
    """DB(REST·RPC)가 돌려주는 모양의 예약 행. 시각은 ISO 문자열이다."""
    row: dict[str, Any] = {
        "id": 7,
        "email": "user@example.com",
        "query": "에어팟",
        "place_name": "서울역",
        "radius_m": 3000,
        "status": "active",
        "link_nonce": "nonce-7",
        "created_at": NOW.isoformat(),
        "expires_at": "2026-11-13T02:00:00+00:00",
        "extension_count": 0,
    }
    row.update(overrides)
    return row


from retriever_lost_found.alerts.sender import OutgoingEmail, SendError  # noqa: E402


class FakeEmailSender:
    """보낸 메일을 기록한다. failures[수신자]에 넣은 오류를 차례로 하나씩 던진다."""

    def __init__(
        self,
        *,
        failures: dict[str, list[SendError]] | None = None,
        open_error: SendError | None = None,
    ) -> None:
        self.sent: list[OutgoingEmail] = []
        self.failures = {address: list(errors) for address, errors in (failures or {}).items()}
        self.open_error = open_error
        self.open_count = 0
        self.closed = 0

    def open(self) -> "FakeEmailSender":
        if self.open_error is not None:
            raise self.open_error
        self.open_count += 1
        return self

    def send(self, email: OutgoingEmail) -> None:
        queued = self.failures.get(email.to)
        if queued:
            raise queued.pop(0)
        self.sent.append(email)

    def close(self) -> None:
        self.closed += 1


import itertools  # noqa: E402
import math  # noqa: E402
from datetime import timedelta  # noqa: E402

from retriever_lost_found.alerts.links import (  # noqa: E402
    EXTENSION_DAYS,
    MAX_EXTENSIONS,
    expires_at_for,
)

SOURCES = ("partner_api", "police_api")
EARTH_RADIUS_METERS = 6371008.7714  # PostGIS 구면 계산 반지름
PUBLIC_RESERVATION_FIELDS = (
    "id", "email", "query", "place_name", "radius_m", "status",
    "link_nonce", "created_at", "expires_at", "extension_count",
)
KIND_ORDER = {"confirm": 0, "match": 1, "expiry_notice": 2}


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


class FakeMonotonic:
    """시간 예산 테스트용 시계. 부를 때마다 step만큼 흐른다."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.value = start
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class FakeAlertStore:
    """마이그레이션 20261106120000의 DB 함수와 같은 규칙을 메모리에서 흉내 낸다."""

    secret_key = "fake-supabase-secret"

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or NOW
        self._ids = itertools.count(1)
        self.reservations: dict[int, dict[str, Any]] = {}
        self.cursors: dict[tuple[int, str], datetime] = {}
        self.messages: dict[int, dict[str, Any]] = {}
        self.notified: dict[tuple[int, int], dict[str, Any]] = {}
        self.locations: dict[int, dict[str, Any]] = {}
        self.found_items: dict[int, dict[str, Any]] = {}
        self.runs: dict[int, dict[str, Any]] = {}

    # ---- 테스트 준비용 ----
    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)

    def add_location(self, location_id, latitude, longitude, *, name="서울역 유실물센터", phone="02-000-0000", is_active=True):
        self.locations[location_id] = {
            "id": location_id, "latitude": latitude, "longitude": longitude,
            "name": name, "phone": phone, "is_active": is_active,
        }

    def add_found_item(
        self, item_id, *, storage_location_id, item_source_code="partner_api", item_name="에어팟",
        raw_category_name="무선이어폰", color_name="흰색", description="", registered_on="2026-11-06",
        first_seen_at=None,
    ):
        self.found_items[item_id] = {
            "id": item_id, "storage_location_id": storage_location_id, "item_source_code": item_source_code,
            "item_name": item_name, "raw_category_name": raw_category_name, "color_name": color_name,
            "description": description, "normalized_search_text": item_name,
            "registered_on": registered_on, "first_seen_at": first_seen_at or self.now,
        }

    def add_active_reservation(
        self, *, email="user@example.com", query="에어팟", latitude=37.5547, longitude=126.9707,
        radius_m=1000, expires_at=None, extension_count=0, link_nonce="nonce-1",
    ) -> int:
        reservation_id = next(self._ids)
        self.reservations[reservation_id] = {
            "id": reservation_id, "email": email, "query": query, "place_name": "서울역",
            "latitude": latitude, "longitude": longitude, "radius_m": radius_m, "status": "active",
            "link_nonce": link_nonce, "ip_hash": "ip-hash", "created_at": self.now,
            "confirmed_at": self.now, "expires_at": expires_at or expires_at_for(self.now),
            "extension_count": extension_count, "ended_at": None,
        }
        for source in SOURCES:
            self.cursors[(reservation_id, source)] = self.now
        return reservation_id

    def add_pending_match(self, reservation_id: int) -> int:
        return self._insert_message(reservation_id, "match")

    def queue_match(self, reservation_id: int, item_ids, *, score: float = 1.0) -> int:
        self.record_match(reservation_id, "partner_api", None, [(item_id, score) for item_id in item_ids])
        return next(
            message["id"] for message in self.messages.values()
            if message["reservation_id"] == reservation_id and message["kind"] == "match"
            and message["status"] == "pending"
        )

    def add_sent_messages(self, count: int, *, sent_at: datetime) -> None:
        for _ in range(count):
            message_id = self._insert_message(0, "confirm")
            self.messages[message_id].update(status="sent", sent_at=sent_at)

    # ---- 내부 ----
    def _insert_message(self, reservation_id, kind, expiry_for=None) -> int:
        message_id = next(self._ids)
        self.messages[message_id] = {
            "id": message_id, "reservation_id": reservation_id, "kind": kind, "status": "pending",
            "expiry_for": expiry_for, "attempts": 0, "claimed_at": None, "sent_at": None,
            "last_error": None, "created_at": self.now,
        }
        return message_id

    def _public_reservation(self, reservation):
        return {field: _iso(reservation[field]) for field in PUBLIC_RESERVATION_FIELDS}

    def _public_message(self, message):
        return {key: _iso(value) for key, value in message.items()}

    def _end(self, reservation, status):
        reservation.update(status=status, email=None, ip_hash=None, ended_at=self.now)
        for message_id in [
            message_id for message_id, message in self.messages.items()
            if message["reservation_id"] == reservation["id"] and message["status"] == "pending"
        ]:
            self.delete_message(message_id)

    # ---- AlertStore ----
    def create_reservation(self, *, email, query, place_name, latitude, longitude, radius_m, ip_hash, link_nonce, expires_at):
        open_count = sum(
            1 for reservation in self.reservations.values()
            if reservation["email"] == email and reservation["status"] in ("pending", "active")
        )
        if open_count >= 3:
            return {"status": "email_limit"}
        if ip_hash is not None and sum(
            1 for reservation in self.reservations.values()
            if reservation["ip_hash"] == ip_hash and reservation["created_at"] > self.now - timedelta(hours=24)
        ) >= 5:
            return {"status": "ip_limit"}
        reservation_id = next(self._ids)
        self.reservations[reservation_id] = {
            "id": reservation_id, "email": email, "query": query.strip(), "place_name": place_name.strip(),
            "latitude": latitude, "longitude": longitude, "radius_m": radius_m, "status": "pending",
            "link_nonce": link_nonce, "ip_hash": ip_hash, "created_at": self.now, "confirmed_at": None,
            "expires_at": expires_at, "extension_count": 0, "ended_at": None,
        }
        for source in SOURCES:
            self.cursors[(reservation_id, source)] = self.now
        message_id = self._insert_message(reservation_id, "confirm")
        return {"status": "created", "reservation_id": reservation_id, "message_id": message_id, "expires_at": _iso(expires_at)}

    def delete_reservation(self, reservation_id):
        self.reservations.pop(reservation_id, None)
        for key in [key for key in self.cursors if key[0] == reservation_id]:
            del self.cursors[key]
        for message_id in [mid for mid, message in self.messages.items() if message["reservation_id"] == reservation_id]:
            self.delete_message(message_id)

    def get_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        return None if reservation is None else self._public_reservation(reservation)

    def confirm_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            return "not_found"
        if reservation["status"] == "active":
            return "already_active"
        if reservation["status"] != "pending":
            return "ended"
        if reservation["created_at"] <= self.now - timedelta(hours=24):
            return "link_expired"
        reservation.update(status="active", confirmed_at=self.now)
        return "confirmed"

    def extend_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            return {"result": "not_found"}
        if reservation["status"] != "active" or reservation["expires_at"] <= self.now:
            return {"result": "ended"}
        if reservation["extension_count"] >= MAX_EXTENSIONS:
            return {"result": "limit_reached"}
        reservation["expires_at"] += timedelta(days=EXTENSION_DAYS)
        reservation["extension_count"] += 1
        return {
            "result": "extended",
            "expires_at": _iso(reservation["expires_at"]),
            "extensions_left": MAX_EXTENSIONS - reservation["extension_count"],
        }

    def cancel_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            return "not_found"
        if reservation["status"] not in ("pending", "active"):
            return "already_ended"
        self._end(reservation, "cancelled")
        return "cancelled"

    def match_queue(self, item_source_code):
        rows = [
            (self.cursors[(reservation["id"], item_source_code)], reservation["id"], reservation)
            for reservation in self.reservations.values()
            if reservation["status"] == "active" and reservation["expires_at"] > self.now
        ]
        rows.sort(key=lambda row: (row[0], row[1]))
        return [{"id": row[2]["id"], "query": row[2]["query"], "checked_until": _iso(row[0])} for row in rows]

    def candidate_items(self, reservation_id, item_source_code):
        reservation = self.reservations.get(reservation_id)
        cursor = self.cursors.get((reservation_id, item_source_code))
        if (
            reservation is None or cursor is None or reservation["status"] != "active"
            or reservation["expires_at"] <= self.now
        ):
            return {"checked_until": None, "items": []}
        scope = {
            location_id for location_id, location in self.locations.items()
            if location["is_active"] and haversine_meters(
                reservation["latitude"], reservation["longitude"], location["latitude"], location["longitude"]
            ) <= reservation["radius_m"]
        }
        items = [
            dict(item) for item in self.found_items.values()
            if item["storage_location_id"] in scope
            and item["item_source_code"] == item_source_code
            and item["first_seen_at"] > cursor
            and (reservation_id, item["id"]) not in self.notified
        ]
        items.sort(key=lambda item: (item["first_seen_at"], item["id"]))
        checked_until = max((item["first_seen_at"] for item in items), default=cursor)
        for item in items:
            item["first_seen_at"] = _iso(item["first_seen_at"])
        return {"checked_until": _iso(checked_until), "items": items}

    def record_match(self, reservation_id, item_source_code, checked_until, items):
        fresh = [(item_id, score) for item_id, score in items if (reservation_id, item_id) not in self.notified]
        queued = 0
        message_id = None
        if fresh:
            pending = next(
                (message for message in self.messages.values()
                 if message["reservation_id"] == reservation_id and message["kind"] == "match"
                 and message["status"] == "pending"),
                None,
            )
            message_id = pending["id"] if pending else self._insert_message(reservation_id, "match")
            for item_id, score in fresh:
                self.notified[(reservation_id, item_id)] = {"message_id": message_id, "score": score}
                queued += 1
        key = (reservation_id, item_source_code)
        if checked_until is not None and key in self.cursors:
            self.cursors[key] = max(self.cursors[key], _parse(checked_until))
        return {"items_queued": queued, "message_id": message_id}

    def enqueue_expiry_notices(self):
        count = 0
        for reservation in self.reservations.values():
            if reservation["status"] != "active":
                continue
            if not (self.now < reservation["expires_at"] <= self.now + timedelta(hours=36)):
                continue
            if any(
                message["reservation_id"] == reservation["id"] and message["kind"] == "expiry_notice"
                and message["expiry_for"] == reservation["expires_at"]
                for message in self.messages.values()
            ):
                continue
            self._insert_message(reservation["id"], "expiry_notice", expiry_for=reservation["expires_at"])
            count += 1
        return count

    def claim_messages(self, *, limit, daily_cap, message_id=None):
        for message in self.messages.values():
            if message["status"] == "sending" and message["claimed_at"] < self.now - timedelta(minutes=10):
                message.update(status="pending", claimed_at=None)
        sent = sum(
            1 for message in self.messages.values()
            if message["status"] == "sent" and message["sent_at"] > self.now - timedelta(hours=24)
        )
        sending = sum(1 for message in self.messages.values() if message["status"] == "sending")
        remaining = daily_cap - sent - sending
        if remaining <= 0:
            return []
        candidates = sorted(
            (message for message in self.messages.values()
             if message["status"] == "pending" and (message_id is None or message["id"] == message_id)),
            key=lambda message: (KIND_ORDER[message["kind"]], message["created_at"], message["id"]),
        )
        claimed = []
        for message in candidates[: min(max(limit, 0), remaining)]:
            message.update(status="sending", claimed_at=self.now, attempts=message["attempts"] + 1)
            claimed.append(self._public_message(message))
        return claimed

    def message_payload(self, message_id):
        message = self.messages.get(message_id)
        if message is None:
            return None
        reservation = self.reservations[message["reservation_id"]]
        items = []
        for (_, item_id), note in self.notified.items():
            if note["message_id"] != message_id:
                continue
            item = self.found_items[item_id]
            location = self.locations.get(item["storage_location_id"], {})
            items.append({
                "id": item_id, "item_name": item["item_name"], "raw_category_name": item["raw_category_name"],
                "color_name": item["color_name"], "registered_on": item["registered_on"],
                "storage_location_name": location.get("name"), "storage_location_phone": location.get("phone"),
                "score": note["score"],
            })
        # SQL과 같은 순서: 점수 높은 순(NULL 마지막) → 최근 등록 순 → id 큰 순
        items.sort(key=lambda item: item["id"], reverse=True)
        items.sort(key=lambda item: item["registered_on"], reverse=True)
        items.sort(key=lambda item: item["score"] if item["score"] is not None else -1.0, reverse=True)
        return {
            "message": self._public_message(message),
            "reservation": self._public_reservation(reservation),
            "items": items,
        }

    def finish_message(self, message_id, status, error=None):
        message = self.messages.get(message_id)
        if message is None or message["status"] != "sending":
            return
        message.update(
            status=status,
            last_error=error,
            sent_at=self.now if status == "sent" else None,
            claimed_at=None if status == "pending" else message["claimed_at"],
        )

    def delete_message(self, message_id):
        self.messages.pop(message_id, None)
        for key in [key for key, note in self.notified.items() if note["message_id"] == message_id]:
            del self.notified[key]

    def close_ended_reservations(self):
        expired = unconfirmed = 0
        for reservation in self.reservations.values():
            if reservation["status"] == "active" and reservation["expires_at"] <= self.now:
                self._end(reservation, "expired")
                expired += 1
            elif reservation["status"] == "pending" and reservation["created_at"] <= self.now - timedelta(hours=24):
                self._end(reservation, "unconfirmed_expired")
                unconfirmed += 1
        return {"expired": expired, "unconfirmed_closed": unconfirmed}

    def start_run(self, kind, item_source_code=None):
        run_id = next(self._ids)
        self.runs[run_id] = {"id": run_id, "kind": kind, "item_source_code": item_source_code, "status": "running"}
        return run_id

    def finish_run(self, run_id, values):
        self.runs[run_id].update(values)
