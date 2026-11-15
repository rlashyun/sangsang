from __future__ import annotations

import unittest

from alert_fakes import NOW, TEST_SETTINGS, reservation_row
from retriever_lost_found.alerts.links import mail_links
from retriever_lost_found.alerts.templates import (
    CONFIRM_SUBJECT,
    EXPIRY_SUBJECT,
    EXTEND_INVITATION,
    FINAL_NOTICE,
    MATCH_HEADLINE,
    MATCH_SUBJECT,
    OWNERSHIP_NOTICE,
    alert_period,
    confirmation_email,
    expiry_notice_email,
    match_email,
    radius_label,
)


def item(index: int) -> dict:
    return {
        "id": index,
        "item_name": f"에어팟 {index}",
        "raw_category_name": "무선이어폰",
        "color_name": "흰색",
        "registered_on": "2026-11-06",
        "storage_location_name": "서울역 유실물센터",
        "storage_location_phone": "02-000-0000",
        "score": 1.0,
    }


def links_for(reservation: dict):
    return mail_links(TEST_SETTINGS, reservation, NOW)


class TemplateTests(unittest.TestCase):
    def test_period_is_d_plus_one_to_d_plus_seven_in_korean_dates(self) -> None:
        # PRD 4.3-E — 11-06(금) 15:00 KST 등록 → 11-07(토)~11-13(금). 만료 시각은 11-14 00:00 KST.
        self.assertEqual(alert_period(reservation_row()), "11월 7일(토)부터 11월 13일(금)까지")
        extended = reservation_row(expires_at="2026-11-20T15:00:00+00:00")
        self.assertEqual(alert_period(extended), "11월 7일(토)부터 11월 20일(금)까지")

    def test_radius_is_written_like_the_screen(self) -> None:
        # PRD 4.3-E — 1.5km가 "1km"로 잘리지 않고, 1·2km에 소수점이 붙지 않는다.
        self.assertEqual([radius_label(m) for m in (1000, 1500, 2000)], ["1km", "1.5km", "2km"])

    def test_confirmation_mail_has_confirm_and_cancel_links(self) -> None:
        reservation = reservation_row(status="pending")
        links = links_for(reservation)

        email = confirmation_email("user@example.com", reservation, links)

        self.assertEqual(email.subject, CONFIRM_SUBJECT)
        self.assertIn(links.confirm, email.text)
        self.assertIn(links.cancel, email.text)
        self.assertIn("24시간", email.text)
        self.assertIn("서울역 반경 1.5km", email.text)

    def test_match_mail_says_similar_and_does_not_claim_ownership(self) -> None:
        reservation = reservation_row()

        email = match_email("user@example.com", reservation, links_for(reservation), [item(1)])

        self.assertEqual(email.subject, MATCH_SUBJECT)
        self.assertIn("유사한 물품이 새로 등록됐습니다", email.text)
        self.assertIn(MATCH_HEADLINE, email.text)
        self.assertIn(OWNERSHIP_NOTICE, email.text)
        self.assertIn("서울역 유실물센터 02-000-0000", email.text)

    def test_item_registration_date_is_written_in_korean(self) -> None:
        reservation = reservation_row()
        dated = {**item(1), "registered_on": "2026-11-15"}
        undated = {**item(2), "registered_on": None}

        email = match_email("user@example.com", reservation, links_for(reservation), [dated, undated])

        self.assertIn("에어팟 1 (무선이어폰, 흰색) · 11월 15일 등록 · 보관:", email.text)
        self.assertNotIn("2026-11-15", email.text)
        self.assertNotIn("None", email.text)
        self.assertIn("2. 에어팟 2 (무선이어폰, 흰색) · 보관:", email.text)

    def test_match_mail_shows_at_most_five_items(self) -> None:
        reservation = reservation_row()
        links = links_for(reservation)

        five = match_email("user@example.com", reservation, links, [item(i) for i in range(1, 6)])
        six = match_email("user@example.com", reservation, links, [item(i) for i in range(1, 7)])

        self.assertIn("에어팟 5", five.text)
        self.assertNotIn("그 밖에", five.text)
        self.assertIn("에어팟 5", six.text)
        self.assertNotIn("에어팟 6", six.text)
        self.assertIn("그 밖에 1개", six.text)

    def test_match_mail_offers_extension_only_while_extensions_remain(self) -> None:
        with_left = reservation_row(extension_count=1)
        used_up = reservation_row(extension_count=3)

        offered = match_email("user@example.com", with_left, links_for(with_left), [item(1)])
        not_offered = match_email("user@example.com", used_up, links_for(used_up), [item(1)])

        self.assertIn("남은 연장 2회", offered.text)
        self.assertIn("/alerts/extend", offered.text)
        self.assertNotIn("/alerts/extend", not_offered.text)

    def test_final_notice_uses_the_prd_wording_and_offers_extension_or_a_new_reservation(self) -> None:
        with_left = reservation_row(extension_count=0)
        used_up = reservation_row(extension_count=3)

        offered = expiry_notice_email("user@example.com", with_left, links_for(with_left))
        not_offered = expiry_notice_email("user@example.com", used_up, links_for(used_up))

        self.assertEqual(offered.subject, EXPIRY_SUBJECT)
        # PRD 4.3-E — 결과가 없어도 종료·연장 안내를 보낸다.
        self.assertIn(f"{FINAL_NOTICE} {EXTEND_INVITATION}", offered.text)
        self.assertIn("오늘 밤 12시 전까지", offered.text)
        self.assertIn("/alerts/extend", offered.text)
        self.assertIn("남은 연장은 3회", offered.text)
        self.assertNotIn(MATCH_HEADLINE, offered.text)
        self.assertIn(FINAL_NOTICE, not_offered.text)
        self.assertNotIn(EXTEND_INVITATION, not_offered.text)
        self.assertNotIn("/alerts/extend", not_offered.text)
        self.assertIn("새로 예약", not_offered.text)

    def test_final_notice_carries_that_days_similar_items(self) -> None:
        reservation = reservation_row()

        email = expiry_notice_email("user@example.com", reservation, links_for(reservation), [item(1), item(2)])

        self.assertIn(FINAL_NOTICE, email.text)
        self.assertIn(MATCH_HEADLINE, email.text)
        self.assertIn(OWNERSHIP_NOTICE, email.text)
        self.assertIn("1. 에어팟 1", email.text)
        self.assertIn("2. 에어팟 2", email.text)

    def test_user_input_is_escaped_in_html_and_kept_out_of_the_subject(self) -> None:
        reservation = reservation_row(query="<script>alert(1)</script>", place_name="서울역 <b>")

        email = match_email("user@example.com", reservation, links_for(reservation), [item(1)])

        self.assertNotIn("<script>", email.html)
        self.assertIn("&lt;script&gt;", email.html)
        self.assertNotIn("<b>", email.html)
        self.assertNotIn("script", email.subject)


if __name__ == "__main__":
    unittest.main()
