"""설계 10.3-2 — DB 없이 메일 한 통만 보내 발송 설정(Gmail SMTP 또는 Resend)과 스팸함 분류를 확인한다.

실행: uv run python -m retriever_lost_found.alerts.send_test --to <받을 주소>
실제 메일이 나가므로 받는 주소를 확인받은 뒤 실행한다. 받는 주소는 어디에도 저장하지 않는다.
"""

from __future__ import annotations

import argparse

from .reservations import normalize_email
from .sender import OutgoingEmail, SendError, create_email_sender
from .settings import load_alert_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="리트리버 알림 메일 발송 테스트(한 통)")
    parser.add_argument("--to", required=True, help="받을 주소")
    args = parser.parse_args(argv)

    settings = load_alert_settings()
    if settings is None:
        print("메일 설정(MAIL_PROVIDER·MAIL_FROM·RESEND_API_KEY 또는 MAIL_SMTP_*, PUBLIC_BASE_URL, ALERT_SECRET)이 .env에 없습니다.")
        return 1
    email = OutgoingEmail(
        to=normalize_email(args.to),
        subject="[리트리버] 알림 메일 발송 테스트",
        text="리트리버 알림 메일 발송 테스트입니다.\n받은편지함에 왔는지, 스팸함·프로모션함으로 갔는지 확인해 주세요.",
        html="<p>리트리버 알림 메일 발송 테스트입니다.</p><p>받은편지함에 왔는지, 스팸함·프로모션함으로 갔는지 확인해 주세요.</p>",
    )
    try:
        session = create_email_sender(settings.mail).open()
        try:
            session.send(email)
        finally:
            session.close()
    except SendError as error:
        print(f"발송 실패: {error}")
        return 1
    print("발송했습니다. 받은편지함과 스팸함을 확인해 주세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
