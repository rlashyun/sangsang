from __future__ import annotations

import unittest

from alert_fakes import NOW, TEST_SETTINGS, reservation_row
from retriever_lost_found.alerts.links import mail_links
from retriever_lost_found.alerts.templates import (
    CONFIRM_SUBJECT,
    EXPIRY_SUBJECT,
    MATCH_HEADLINE,
    MATCH_SUBJECT,
    OWNERSHIP_NOTICE,
    confirmation_email,
    expiry_notice_email,
    format_expiry,
    match_email,
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
    def test_expiry_is_written_in_korean_time(self) -> None:
        self.assertEqual(format_expiry("2026-11-13T02:00:00+00:00"), "11월 13일(금) 오전 11시")

    def test_confirmation_mail_has_confirm_and_cancel_links(self) -> None:
        reservation = reservation_row(status="pending")
        links = links_for(reservation)

        email = confirmation_email("user@example.com", reservation, links)

        self.assertEqual(email.subject, CONFIRM_SUBJECT)
        self.assertIn(links.confirm, email.text)
        self.assertIn(links.cancel, email.text)
        self.assertIn("24시간", email.text)
        self.assertIn("서울역 반경 3km", email.text)

    def test_match_mail_says_similar_and_does_not_claim_ownership(self) -> None:
        reservation = reservation_row()

        email = match_email("user@example.com", reservation, links_for(reservation), [item(1)])

        self.assertEqual(email.subject, MATCH_SUBJECT)
        self.assertIn("유사한 물품이 새로 등록됐습니다", email.text)
        self.assertIn(MATCH_HEADLINE, email.text)
        self.assertIn(OWNERSHIP_NOTICE, email.text)
        self.assertIn("서울역 유실물센터 02-000-0000", email.text)

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

    def test_expiry_notice_offers_extension_or_a_new_reservation(self) -> None:
        with_left = reservation_row(extension_count=0)
        used_up = reservation_row(extension_count=3)

        offered = expiry_notice_email("user@example.com", with_left, links_for(with_left))
        not_offered = expiry_notice_email("user@example.com", used_up, links_for(used_up))

        self.assertEqual(offered.subject, EXPIRY_SUBJECT)
        self.assertIn("/alerts/extend", offered.text)
        self.assertIn("남은 연장은 3회", offered.text)
        self.assertNotIn("/alerts/extend", not_offered.text)
        self.assertIn("새로 예약", not_offered.text)

    def test_user_input_is_escaped_in_html_and_kept_out_of_the_subject(self) -> None:
        reservation = reservation_row(query="<script>alert(1)</script>", place_name="서울역 <b>")

        email = match_email("user@example.com", reservation, links_for(reservation), [item(1)])

        self.assertNotIn("<script>", email.html)
        self.assertIn("&lt;script&gt;", email.html)
        self.assertNotIn("<b>", email.html)
        self.assertNotIn("script", email.subject)


if __name__ == "__main__":
    unittest.main()
