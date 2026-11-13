import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { DEFAULT_SEARCH_RADIUS_METERS, radiusKilometersLabel, SEARCH_RADIUS_OPTIONS_METERS } from "../lib/geo.js";
import type { Place } from "../types";

interface NewItemAlertProps {
  place: Place | null;
  query: string;
  radiusMeters: number;
  onClose: () => void;
}

// PRD 4.3-E — 검색 조건을 재입력하지 않는다. API 연결 전에는 저장/메일 발송을 흉내 내지 않는다.
export function NewItemAlert({ place, query, radiusMeters, onClose }: NewItemAlertProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pointerOutside = useRef(false);
  const [email, setEmail] = useState("");
  const [notice, setNotice] = useState("");
  const [alertRadius, setAlertRadius] = useState(radiusMeters);
  const [editing, setEditing] = useState(false);
  const [draftRadius, setDraftRadius] = useState(radiusMeters);
  const radiusButtonRef = useRef<HTMLButtonElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const hasConditions = place !== null && query !== "";

  useEffect(() => { if (editing) radiusButtonRef.current?.focus(); }, [editing]);

  function finishEditing(apply: boolean) {
    if (apply) {
      setAlertRadius(draftRadius);
      setNotice("");
    }
    setEditing(false);
    requestAnimationFrame(() => editButtonRef.current?.focus());
  }

  useEffect(() => {
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    const searchSurface = document.querySelector<HTMLElement>(".app-shell");
    const previouslyInert = searchSurface?.inert ?? false;
    if (searchSurface) searchSurface.inert = true;
    const dialog = dialogRef.current;
    dialog?.show();
    dialog?.querySelector<HTMLButtonElement>(".item-detail-close")?.focus();
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
    };
    document.addEventListener("keydown", handleEscape);
    document.body.style.overflow = "hidden";
    return () => {
      dialog?.close();
      document.removeEventListener("keydown", handleEscape);
      if (searchSurface) searchSurface.inert = previouslyInert;
      document.body.style.overflow = previousOverflow;
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);

  function outside(event: { clientX: number; clientY: number }) {
    const bounds = dialogRef.current?.getBoundingClientRect();
    return !!bounds && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("아직 알림 신청 기능을 연결하고 있어요. 입력한 이메일은 저장되지 않았고, 확인 메일도 발송되지 않았어요.");
  }

  return createPortal(
    <div className="alert-backdrop"
      onPointerDown={event => { pointerOutside.current = outside(event); }}
      onClick={event => {
        if (event.target === event.currentTarget && pointerOutside.current && outside(event)) onClose();
        pointerOutside.current = false;
      }}>
    <dialog ref={dialogRef} className="new-item-alert" aria-labelledby="new-item-alert-title">
      <header className="alert-header">
        <div><p className="alert-eyebrow">알림 조건 확인</p><h2 id="new-item-alert-title">새 물건 알림</h2></div>
        <button type="button" className="item-detail-close" onClick={onClose} autoFocus>닫기 <span aria-hidden="true">×</span></button>
      </header>
      {hasConditions ? <div className="alert-columns">
        <section className="alert-summary" aria-labelledby="alert-summary-title">
          <h3 id="alert-summary-title">이런 물건을 찾고 있어요</h3>
          <form className="alert-condition-form" onSubmit={event => { event.preventDefault(); finishEditing(true); }}>
          <dl>
            <div><dt>찾는 물건</dt><dd className="alert-query">{query}</dd></div>
            <div><dt>기준 장소</dt><dd>{place.name}<span className="alert-address">{place.address || "주소 정보 없음"}</span></dd></div>
            <div><dt>탐색 반경</dt><dd>{editing
              ? <div className="alert-radius-options" role="group" aria-label="알림 탐색 반경">{SEARCH_RADIUS_OPTIONS_METERS.map(radius =>
                <button ref={radius === DEFAULT_SEARCH_RADIUS_METERS ? radiusButtonRef : undefined} type="button" key={radius} aria-pressed={draftRadius === radius} onClick={() => setDraftRadius(radius)}>{radiusKilometersLabel(radius)}</button>)}</div>
              : radiusKilometersLabel(alertRadius)}</dd></div>
            <div><dt>알림 기간</dt><dd>등록 다음날부터 7일간<span className="alert-address">한국시간 기준</span></dd></div>
          </dl>
          {editing ? <>
            <p className="alert-help">반경 변경은 알림에만 적용돼요. 장소나 물품을 바꾸려면 물건 찾기에서 다시 검색해주세요.</p>
            <div className="alert-edit-actions"><button type="button" onClick={() => finishEditing(false)}>취소</button><button type="submit">수정 완료</button></div>
          </> : <button ref={editButtonRef} className="alert-edit" type="button" onClick={() => {
            setDraftRadius(alertRadius); setEditing(true);
          }}>알림 조건 수정하기</button>}
          </form>
        </section>
        <section className="alert-registration" aria-labelledby="alert-email-title">
          <h3 id="alert-email-title">새로 등록되면 알려드릴게요</h3>
          <p>매일 데이터 갱신 후, 이 조건과 유사한 새 물품이 있으면 이메일로 알려드려요.</p>
          <form onSubmit={submit}>
            <label htmlFor="alert-email">알림 받을 이메일</label>
            <input id="alert-email" type="email" required autoComplete="email" maxLength={254} placeholder="name@example.com"
              value={email} onChange={event => { setEmail(event.target.value); setNotice(""); }} aria-describedby="alert-api-note alert-email-help" />
            <p id="alert-email-help" className="alert-help">연결 후에는 메일의 확인 링크를 눌러야 알림이 시작돼요.</p>
            <button className="alert-submit" type="submit" disabled={editing}>이 조건으로 알림 받기</button>
            <p id="alert-api-note" className="alert-preview-note">화면 미리보기 · 아직 신청과 메일 발송은 지원하지 않아요.</p>
            <p role="status" className="alert-submit-notice">{notice}</p>
          </form>
          <div className="alert-schedule"><p>새 유사 물품이 없는 날에는 보내지 않아요.</p><p>마지막 날에는 종료와 연장 방법을 안내해요.</p></div>
        </section>
      </div> : <div className="alert-empty"><h3>먼저 찾는 물건을 검색해주세요</h3><p>장소를 선택하고 물품을 검색하면, 그 조건을 그대로 가져올게요.</p><button className="alert-submit" type="button" onClick={onClose}>물건 찾기로 돌아가기</button></div>}
    </dialog></div>, document.body,
  );
}
