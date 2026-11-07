from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from alert_fakes import NOW, TEST_SETTINGS, reservation_row
from retriever_lost_found.alerts.links import (
    KOREA_TIMEZONE,
    expires_at_for,
    ip_hash,
    link_token,
    mail_links,
    new_link_nonce,
    token_matches,
    token_reservation_id,
)


class LinkTokenTests(unittest.TestCase):
    def test_token_round_trips_and_rejects_tampering(self) -> None:
        token = link_token("secret", 7, "nonce-7")

        self.assertTrue(token.startswith("7."))
        self.assertTrue(token_matches("secret", token, "nonce-7"))
        self.assertFalse(token_matches("secret", token, "other-nonce"))
        self.assertFalse(token_matches("other-secret", token, "nonce-7"))
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertFalse(token_matches("secret", tampered, "nonce-7"))

    def test_malformed_token_is_a_bad_link(self) -> None:
        for token in ("", "abc", "7.", "0.0123456789abcdef0123456789abcdef", "7.XYZ"):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "링크가 올바르지 않습니다"):
                token_reservation_id(token)

    def test_nonce_is_random(self) -> None:
        first = new_link_nonce()

        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, new_link_nonce())


class IpHashTests(unittest.TestCase):
    def test_same_ip_same_hash_and_the_raw_ip_is_not_kept(self) -> None:
        first = ip_hash("secret", "203.0.113.7")

        self.assertEqual(first, ip_hash("secret", " 203.0.113.7 "))
        self.assertNotIn("203.0.113.7", first)
        self.assertNotEqual(first, ip_hash("other-secret", "203.0.113.7"))

    def test_missing_ip_is_not_hashed(self) -> None:
        self.assertIsNone(ip_hash("secret", "  "))


class ExpiryTests(unittest.TestCase):
    def test_expiry_is_eleven_kst_seven_days_after_registration(self) -> None:
        # 11-06 15:00 KST 등록 → 11-13 11:00 KST (설계 5.3)
        registered = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)

        self.assertEqual(
            expires_at_for(registered),
            datetime(2026, 11, 13, 11, 0, tzinfo=KOREA_TIMEZONE),
        )

    def test_expiry_counts_from_the_korean_date_not_the_utc_date(self) -> None:
        # 11-06 00:30 KST는 UTC로 아직 11-05다. 한국 날짜 기준이면 11-13, UTC 기준이면 11-12가 된다.
        registered = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)

        self.assertEqual(
            expires_at_for(registered),
            datetime(2026, 11, 13, 11, 0, tzinfo=KOREA_TIMEZONE),
        )


class MailLinksTests(unittest.TestCase):
    def test_pending_reservation_gets_confirm_and_cancel_links_only(self) -> None:
        links = mail_links(TEST_SETTINGS, reservation_row(status="pending"), NOW)
        token = link_token(TEST_SETTINGS.secret, 7, "nonce-7")

        self.assertEqual(links.confirm, f"https://retriever.example/alerts/confirm?token={token}")
        self.assertEqual(links.cancel, f"https://retriever.example/alerts/cancel?token={token}")
        self.assertEqual(links.home, "https://retriever.example/")
        self.assertIsNone(links.extend)

    def test_extend_link_only_while_extensions_remain(self) -> None:
        self.assertIsNotNone(mail_links(TEST_SETTINGS, reservation_row(extension_count=2), NOW).extend)
        self.assertIsNone(mail_links(TEST_SETTINGS, reservation_row(extension_count=3), NOW).extend)
        self.assertIsNone(mail_links(TEST_SETTINGS, reservation_row(), NOW).confirm)

    def test_no_extend_link_after_expiry(self) -> None:
        expired = reservation_row(expires_at=(NOW - timedelta(minutes=1)).isoformat())

        self.assertIsNone(mail_links(TEST_SETTINGS, expired, NOW).extend)


if __name__ == "__main__":
    unittest.main()
