from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import AlertLimitError
from .links import (
    KOREA_TIMEZONE,
    MAX_EXTENSIONS,
    expires_at_for,
    ip_hash,
    mail_links,
    new_link_nonce,
    parse_timestamp,
    token_matches,
    token_reservation_id,
)
from .sender import EmailSender, SendError
from .settings import AlertSettings
from .store import AlertStore
from .templates import confirmation_email, format_expiry

ALLOWED_RADII_METERS = (1000, 3000, 5000)  # PRD 3.1-3 · 4.2 — 화면의 1·3·5km
MAX_QUERY_LENGTH = 30  # PRD 3.3 · D3 — 습득물 검색어 상한
MAX_PLACE_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
# 설계 14장 — 서비스 대상인 대한민국(독도 포함) 밖 좌표로는 예약하지 않는다.
LATITUDE_RANGE = (33.0, 38.7)
LONGITUDE_RANGE = (124.5, 132.0)
PENDING_MESSAGE = "확인 메일을 보냈습니다. 24시간 안에 링크를 눌러 주세요."
BAD_LINK = "링크가 올바르지 않습니다."
_FORBIDDEN_EMAIL_CHARACTERS = set('<>,;:"()[]\\')


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: Any) -> str:
    """설계 5.4 — 앞뒤 공백을 지우고 소문자로 저장해 이메일 제한을 대소문자로 피하지 못하게 한다."""
    if not isinstance(value, str):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    email = value.strip().lower()
    local, _, domain = email.partition("@")
    if (
        not email
        or len(email) > MAX_EMAIL_LENGTH
        or email.count("@") != 1
        or not local
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or any(character.isspace() for character in email)
        or any(character in _FORBIDDEN_EMAIL_CHARACTERS for character in email)
    ):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    return email


def _query(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("검색어를 입력해 주세요.")
    query = value.strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("습득물 검색어는 30자 이하여야 합니다.")
    return query


def _place_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("장소 이름이 없습니다.")
    place_name = value.strip()
    if len(place_name) > MAX_PLACE_NAME_LENGTH:
        raise ValueError("장소 이름은 100자 이하여야 합니다.")
    return place_name


def _coordinate(value: Any, name: str, bounds: tuple[float, float]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 좌표가 숫자가 아닙니다.")
    number = float(value)
    if not bounds[0] <= number <= bounds[1]:  # NaN도 여기서 걸린다
        raise ValueError("대한민국 안의 장소만 예약할 수 있습니다.")
    return number


def _radius(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in ALLOWED_RADII_METERS:
        raise ValueError("반경은 1km, 3km, 5km 중 하나여야 합니다.")
    return value


@dataclass
class AlertService:
    """설계 5.4 — 예약 API의 규칙. 저장·발송은 주입받는다(테스트는 가짜를 쓴다)."""

    store: AlertStore
    sender: EmailSender
    settings: AlertSettings
    clock: Callable[[], datetime] = utc_now

    def create(self, payload: dict[str, Any], client_ip: str) -> dict[str, str]:
        email = normalize_email(payload.get("email"))
        query = _query(payload.get("query"))
        place_name = _place_name(payload.get("place_name"))
        latitude = _coordinate(payload.get("latitude"), "위도", LATITUDE_RANGE)
        longitude = _coordinate(payload.get("longitude"), "경도", LONGITUDE_RANGE)
        radius_m = _radius(payload.get("radius_m"))
        if payload.get("consent") is not True:
            raise ValueError("개인정보 수집·이용에 동의해야 알림을 예약할 수 있습니다.")

        now = self.clock()
        created = self.store.create_reservation(
            email=email,
            query=query,
            place_name=place_name,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            ip_hash=ip_hash(self.settings.secret, client_ip),
            link_nonce=new_link_nonce(),
            expires_at=expires_at_for(now),
        )
        status = created.get("status")
        if status == "email_limit":
            raise AlertLimitError("같은 이메일로 진행 중인 알림 예약은 3개까지입니다.")
        if status == "ip_limit":
            raise AlertLimitError("같은 곳에서 하루에 만들 수 있는 알림 예약은 5개까지입니다. 내일 다시 시도해 주세요.")
        if status != "created":
            raise RuntimeError("알림 예약을 저장하지 못했습니다.")
        reservation_id = int(created["reservation_id"])
        message_id = int(created["message_id"])

        # 설계 2장 — 확인 메일도 하루 450통 안에서 센다.
        if not self.store.claim_messages(limit=1, daily_cap=self.settings.daily_send_limit, message_id=message_id):
            self.store.delete_reservation(reservation_id)
            raise AlertLimitError("오늘 보낼 수 있는 메일을 모두 보냈습니다. 내일 다시 시도해 주세요.")
        reservation = self.store.get_reservation(reservation_id)
        if reservation is None:
            raise RuntimeError("알림 예약을 저장하지 못했습니다.")
        message = confirmation_email(email, reservation, mail_links(self.settings, reservation, now))
        try:
            session = self.sender.open()
            try:
                session.send(message)
            finally:
                session.close()
        except SendError:
            # 설계 5.4 — 발송에 실패하면 예약을 남기지 않아 사용자가 바로 다시 시도할 수 있다.
            self.store.delete_reservation(reservation_id)
            raise RuntimeError("확인 메일을 보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.") from None
        self.store.finish_message(message_id, "sent")
        return {"status": "pending", "message": PENDING_MESSAGE}

    def _reservation(self, token: Any) -> dict[str, Any]:
        if not isinstance(token, str):
            raise ValueError(BAD_LINK)
        reservation = self.store.get_reservation(token_reservation_id(token))
        if reservation is None or not token_matches(self.settings.secret, token, str(reservation["link_nonce"])):
            raise ValueError(BAD_LINK)
        return reservation

    def summary(self, token: Any) -> dict[str, Any]:
        """설계 5.4 — 링크 페이지의 요약. 상태를 바꾸지 않는다(링크를 열기만 해서는 처리하지 않는다)."""
        reservation = self._reservation(token)
        return {
            "status": reservation["status"],
            "place_name": reservation["place_name"],
            "radius_m": int(reservation["radius_m"]),
            "query": reservation["query"],
            "expires_at": parse_timestamp(reservation["expires_at"]).astimezone(KOREA_TIMEZONE).isoformat(),
            "extensions_left": MAX_EXTENSIONS - int(reservation["extension_count"]),
        }

    def confirm(self, token: Any) -> dict[str, str]:
        reservation = self._reservation(token)
        result = self.store.confirm_reservation(int(reservation["id"]))
        messages = {
            "confirmed": f"알림 예약이 켜졌습니다. {format_expiry(reservation['expires_at'])}까지 알려 드립니다.",
            "already_active": "이미 켜져 있는 예약입니다.",
            "link_expired": "확인 링크의 유효 시간(24시간)이 지났습니다. 다시 예약해 주세요.",
            "ended": "이미 끝난 예약입니다.",
        }
        if result not in messages:
            raise ValueError(BAD_LINK)
        return {"result": result, "message": messages[result]}

    def extend(self, token: Any) -> dict[str, Any]:
        reservation = self._reservation(token)
        outcome = self.store.extend_reservation(int(reservation["id"]))
        result = outcome.get("result")
        if result == "extended":
            expires_at = parse_timestamp(outcome["expires_at"]).astimezone(KOREA_TIMEZONE)
            left = int(outcome["extensions_left"])
            return {
                "result": "extended",
                "message": f"알림을 {format_expiry(expires_at)}까지 연장했습니다. 남은 연장은 {left}회입니다.",
                "expires_at": expires_at.isoformat(),
                "extensions_left": left,
            }
        if result == "limit_reached":
            return {"result": result, "message": "연장할 수 있는 횟수(3회)를 모두 사용했습니다."}
        if result == "ended":
            return {"result": result, "message": "끝났거나 아직 확인하지 않은 예약은 연장할 수 없습니다."}
        raise ValueError(BAD_LINK)

    def cancel(self, token: Any) -> dict[str, str]:
        reservation = self._reservation(token)
        result = self.store.cancel_reservation(int(reservation["id"]))
        if result == "cancelled":
            return {"result": result, "message": "알림 예약을 취소했습니다. 이메일 주소도 지웠습니다."}
        if result == "already_ended":
            return {"result": result, "message": "이미 끝난 예약입니다."}
        raise ValueError(BAD_LINK)
