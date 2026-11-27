// PRD 4.3-E — 메일 링크를 같은 브라우저의 신청 대화와 연결한다.
// 이메일·사진·링크 토큰은 저장하지 않고 24시간 뒤 복원하지 않는다.
const PREFIX = "retriever-alert-return:";
export const ALERT_CONFIRMED_EVENT = "retriever-alert-confirmed";
const ALERT_CONFIRMATION_ACK = "retriever-alert-confirmation-ack";
export const CONFIRMED_TEXT = "확인되었습니다. 알림이 시작됐어요.";
export const ALREADY_ACTIVE_TEXT = "확인되었습니다. 이미 알림이 켜져 있어요.";
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
export function publishAlertConfirmation(id: number, alreadyActive: boolean): Promise<boolean> {
  return new Promise(resolve => {
    const requestId = crypto.randomUUID();
    function finish(delivered: boolean) {
      window.clearTimeout(timeout);
      window.removeEventListener("storage", onAck);
      resolve(delivered);
    }
    function onAck(event: StorageEvent) {
      if (event.key === ALERT_CONFIRMATION_ACK && event.newValue === requestId) finish(true);
    }
    // PRD 4.3-E — 원래 대화가 응답할 시간을 준 뒤에만 저장된 대화로 복귀한다.
    const timeout = window.setTimeout(() => finish(false), 1000);
    window.addEventListener("storage", onAck);
    try { localStorage.setItem(ALERT_CONFIRMED_EVENT, JSON.stringify({ id, requestId, alreadyActive, at: Date.now() })); }
    catch { finish(false); }
  });
}

export function acknowledgeAlertConfirmation(requestId: string) {
  try { localStorage.setItem(ALERT_CONFIRMATION_ACK, requestId); }
  catch { /* 원래 대화에 결과를 표시하고 확인 탭은 복원 경로를 사용한다. */ }
}
