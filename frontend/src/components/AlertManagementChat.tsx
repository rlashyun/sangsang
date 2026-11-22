import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchAlertSummary, manageAlertReservation, type AlertSummary } from "../api/client";
import { radiusKilometersLabel } from "../lib/geo.js";

// PRD 4.3-E — 메일의 연장·취소 링크는 챗봇에서 예약을 확인하고 버튼을 누를 때만 처리한다.
export function AlertManagementChat({ action }: { action: "extend" | "cancel" }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => new URLSearchParams(window.location.search).get("token") || "");
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [finished, setFinished] = useState(false);
  const [conversation, setConversation] = useState<{ author: "user" | "agent"; text: string }[]>([]);
  const pending = useRef(false);
  const resultAnchor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 토큰은 메모리에만 보관하고 주소·이용안내 링크·검색 세션에는 남기지 않는다.
    navigate(location.pathname, { replace: true, state: null });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (!token) { setLoading(false); setError("메일의 예약 링크를 다시 열어 주세요."); return; }
    void fetchAlertSummary(token, controller.signal).then(value => {
      if (!controller.signal.aborted) setSummary(value);
    }).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error && !(error instanceof TypeError) ? error.message : "예약 정보를 불러오지 못했어요. 메일의 링크를 다시 열어 주세요.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [token]);
  useEffect(() => { resultAnchor.current?.scrollIntoView({ block: "nearest" }); }, [summary, conversation, error]);

  async function perform() {
    if (pending.current || finished || !summary) return;
    pending.current = true; setSending(true); setError("");
    setConversation([{ author: "user", text: action === "extend" ? "알림을 7일 더 받을게요." : "이 알림을 취소할게요." }]);
    try {
      const result = await manageAlertReservation(action, token);
      const allowed = action === "extend" ? ["extended", "limit_reached", "ended"] : ["cancelled", "already_ended"];
      if (!allowed.includes(result.result)) throw new Error("예약 처리 결과를 확인하지 못했어요.");
      if (result.result === "extended" && typeof result.last_day === "string" && typeof result.expires_at === "string" && typeof result.extensions_left === "number") {
        setSummary(previous => previous && { ...previous, last_day: result.last_day!, expires_at: result.expires_at!, extensions_left: result.extensions_left! });
      }
      setConversation(previous => [...previous, { author: "agent", text: result.message }]);
      setFinished(true);
    } catch (error) {
      setError(`${error instanceof Error && !(error instanceof TypeError) ? error.message : "연결이 끊겨 처리 결과를 확인하지 못했어요."} 메일의 링크를 다시 열어 예약 상태를 확인해 주세요.`);
      // 응답 유실 뒤 연장을 중복 실행하지 않도록 이 화면에서는 다시 POST하지 않는다.
      setFinished(true);
    } finally { pending.current = false; setSending(false); }
  }
  const active = summary?.status === "active";
  const cancellable = active || summary?.status === "pending";
  const canAct = action === "cancel" ? cancellable : active && (summary?.extensions_left ?? 0) > 0;
  return <>
    {loading && <p className="agent-bubble agent-bubble-agent" role="status">메일에 연결된 알림 예약을 확인하고 있어요.</p>}
    {summary && <div className="agent-bubble agent-bubble-agent agent-alert-conditions agent-mail-summary" aria-label="메일에 연결된 알림 예약">
      <p>{finished || !canAct ? "메일에 연결된 알림 예약이에요." : action === "extend" ? "이 알림을 7일 더 받아보시겠어요?" : "이 알림을 취소하시겠어요? 취소하면 이메일 주소도 삭제돼요."}</p>
      <dl className="agent-alert-fields">
        <div><dt>물건</dt><dd>{summary.query}</dd></div>
        <div><dt>장소</dt><dd>{summary.places.map(place => place.name).join(", ")}</dd></div>
        <div><dt>반경</dt><dd>{radiusKilometersLabel(summary.radius_m)}</dd></div>
        <div><dt>마지막 날</dt><dd>{summary.last_day.replaceAll("-", ".")}</dd></div>
      </dl>
      {action === "extend" && <p>7일씩 최대 3번 연장해 총 28일 동안 알림을 받을 수 있어요. 남은 연장은 {summary.extensions_left}회예요.</p>}
      {!canAct && <p>{!cancellable ? "이미 끝난 예약이에요." : !active ? "메일 확인을 마친 예약만 연장할 수 있어요." : "연장 가능한 횟수를 모두 사용했어요."}</p>}
    </div>}
    {conversation.map((message, index) => <div className={`agent-bubble agent-bubble-${message.author}`} key={index}><p className="agent-message-text">{message.text}</p></div>)}
    {error && <p className="agent-bubble agent-bubble-agent" role="alert">{error}</p>}
    {sending && <p className="agent-bubble agent-bubble-agent" role="status">{action === "extend" ? "알림을 연장하고 있어요." : "알림을 취소하고 있어요."}</p>}
    {!loading && !finished && <div className="agent-place-replies" aria-label="알림 예약 관리">
      {canAct && <button type="button" className="agent-action-chip" disabled={sending} onClick={() => void perform()}>
        {action === "extend" ? "알림 7일 연장하기" : "알림 취소하기"}
      </button>}
      <button type="button" className="agent-action-chip" disabled={sending} onClick={() => {
        setFinished(true); setConversation(previous => [...previous, { author: "agent", text: "예약은 변경하지 않았어요. 물건 찾기를 계속하실 수 있어요." }]);
      }}>{action === "extend" ? "지금은 연장하지 않기" : "알림 유지하기"}</button>
    </div>}
    <div ref={resultAnchor} />
  </>;
}
