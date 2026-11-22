import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CONFIRMED_TEXT, publishAlertConfirmation, readAlertChat } from "../lib/alert-return";

// PRD 4.3-E — 2026-11-22 사용자 요청: 메일 링크에서 POST 확인 후 바로 대화로 복귀한다.
// 서버 GET은 읽기 전용으로 유지한다.
export function AlertConfirm() {
  const navigate = useNavigate();
  const [token] = useState(() => new URLSearchParams(window.location.search).get("token") || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  useEffect(() => {
    // 서버의 no-referrer 헤더에 더해 주소에서도 토큰을 제거한다. 새로고침 시에는 메일을 다시 연다.
    window.history.replaceState(null, "", window.location.pathname);
    if (token) void confirm();
  }, []);
  async function confirm() {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError("");
    try {
      const response = await fetch("/api/alerts/confirm", {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ token }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "확인하지 못했어요. 다시 시도해 주세요.");
      if (result.result !== "confirmed" && result.result !== "already_active") {
        throw new Error(result.message || "이 예약을 확인할 수 없어요.");
      }
      const id = Number(token.split(".")[0]);
      const messages = readAlertChat(id);
      publishAlertConfirmation(id);
      navigate("/", { replace: true, state: { alertReturn: { messages, text: result.result === "confirmed" ? CONFIRMED_TEXT : "확인되었습니다. 이미 알림이 켜져 있어요." } } });
    } catch (error) {
      setError(error instanceof Error && !(error instanceof TypeError) ? error.message : "연결하지 못했어요. 다시 시도해 주세요.");
    } finally { pending.current = false; setBusy(false); }
  }
  return <>
    <header className="app-header"><a className="app-wordmark" href="/">RETRIEVER</a></header>
    <main className="app-shell agent-shell"><section className="agent-panel" aria-label="알림 이메일 확인">
      <div className="agent-feed" role="status"><div className="agent-bubble agent-bubble-agent"><p>이메일을 확인하고 있어요. 완료되면 대화로 돌아갈게요.</p>
        {error && <button type="button" className="agent-place-confirm" disabled={busy || !token} onClick={() => void confirm()}>{busy ? "확인하고 있어요…" : "다시 확인하기"}</button>}
        {!token && <p role="alert">메일의 확인 링크를 다시 열어 주세요.</p>}
        {error && <p role="alert">{error}</p>}
      </div></div>
    </section></main>
  </>;
}
