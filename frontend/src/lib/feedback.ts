// 의견 보내기는 외부 폼(예: 구글 폼)을 새 창으로 연다. 주소는 공개 값이라 VITE_ 환경 변수로 받는다.
// https 주소만 허용해 javascript: 같은 값이 링크로 들어가지 않게 하고, 비어 있으면 null을 돌려준다.
export function feedbackFormUrl(): string | null {
  const value = String(import.meta.env.VITE_FEEDBACK_FORM_URL ?? "").trim();
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}
