// PRD 4.3-E — 메일 링크를 같은 브라우저의 신청 대화와 연결한다.
// 이메일·사진·링크 토큰은 저장하지 않고 24시간 뒤 복원하지 않는다.
const PREFIX = "retriever-alert-return:";
export const ALERT_CONFIRMED_EVENT = "retriever-alert-confirmed";
export const CONFIRMED_TEXT = "확인되었습니다. 알림이 시작됐어요.";
export function rememberAlertChat(id: number, messages: unknown[]) {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(PREFIX)) {
        try { if (JSON.parse(localStorage.getItem(key)!).expires < Date.now()) localStorage.removeItem(key); }
        catch { localStorage.removeItem(key); }
      }
    }
    localStorage.setItem(PREFIX + id, JSON.stringify({ expires: Date.now() + 86400000, messages }));
  } catch { /* 저장 제한이 있어도 메일 확인은 계속 진행한다. */ }
}
export function readAlertChat(id: number): unknown[] {
  try {
    const entry = JSON.parse(localStorage.getItem(PREFIX + id) || "null");
    return entry?.expires > Date.now() && Array.isArray(entry.messages) ? entry.messages : [];
  } catch { return []; }
}
export function publishAlertConfirmation(id: number) {
  try { localStorage.setItem(ALERT_CONFIRMED_EVENT, JSON.stringify({ id, at: Date.now() })); }
  catch { /* 다른 탭 연결이 불가능해도 현재 대화에 결과를 표시한다. */ }
}
