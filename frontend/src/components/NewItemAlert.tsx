import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { DEFAULT_SEARCH_RADIUS_METERS, radiusKilometersLabel, SEARCH_RADIUS_OPTIONS_METERS } from "../lib/geo.js";
import type { Place } from "../types";

const CONSENT_DETAILS = {
  alert: {
    title: "알림을 위한 개인정보 수집·이용",
    items: "이메일 주소, 알림에 포함한 장소 정보(좌표 포함), 탐색 반경, 물품 검색어",
    purpose: "이메일 주소 확인, 저장한 조건으로 새 유사 습득물 검색, 알림과 종료·연장 안내 발송",
    retention: "등록한 이메일 주소는 알림 종료 다음 날 삭제할 예정입니다. 연장한 경우에는 연장된 종료일을 기준으로 합니다. 그 외 신청 정보의 보유 기간은 실제 알림 신청을 받기 전에 안내합니다.",
    refusal: "동의를 거부할 수 있어요. 동의하지 않아도 물건 찾기는 이용할 수 있고, 이메일 알림 신청은 제한돼요.",
  },
  search: {
    title: "검색 품질 개선을 위한 검색어 이용",
    items: "이번 알림 신청에 사용한 물품 검색어",
    purpose: "검색어를 분석해 검색 결과의 정확도를 높이는 데 사용해요.",
    retention: "미정. 검색 품질 개선에 사용하기 전에 보유 기간을 안내할 예정이에요.",
    refusal: "동의하지 않아도 물건 찾기와 새 물건 알림을 이용할 수 있어요.",
  },
};

interface NewItemAlertProps {
  places: readonly Place[];
  query: string;
  radiusMeters: number;
  onClose: () => void;
}

// PRD 4.3-E — 검색 조건을 재입력하지 않는다. API 연결 전에는 저장/메일 발송을 흉내 내지 않는다.
export function NewItemAlert({ places, query, radiusMeters, onClose }: NewItemAlertProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const consentDialogRef = useRef<HTMLDialogElement>(null);
  const [consentKey, setConsentKey] = useState<keyof typeof CONSENT_DETAILS | null>(null);
  const consent = consentKey ? CONSENT_DETAILS[consentKey] : null;
  const pointerOutside = useRef(false);
  const [email, setEmail] = useState("");
  const [notice, setNotice] = useState("");
  const [alertRadius, setAlertRadius] = useState(radiusMeters);
  const [alertPlaces, setAlertPlaces] = useState(places);
  const [editing, setEditing] = useState(false);
  const [draftRadius, setDraftRadius] = useState(radiusMeters);
  const [draftPlaces, setDraftPlaces] = useState(places);
  const radiusButtonRef = useRef<HTMLButtonElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const hasConditions = alertPlaces.length > 0 && query !== "";
  const shownPlaces = editing ? draftPlaces : alertPlaces;

  useEffect(() => { if (editing) radiusButtonRef.current?.focus(); }, [editing]);
  useEffect(() => {
    const dialog = consentDialogRef.current;
    if (consentKey && dialog && !dialog.open) dialog.showModal();
    if (!consentKey && dialog?.open) dialog.close();
  }, [consentKey]);

  function finishEditing(apply: boolean) {
    if (apply) {
      setAlertRadius(draftRadius);
      setAlertPlaces(draftPlaces);
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
      if (event.key === "Escape" && !consentDialogRef.current?.open) { event.preventDefault(); onClose(); }
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
            <div><dt>기준 장소</dt><dd>
              <ul className="alert-place-list">
                {shownPlaces.map(place => <li key={`${place.latitude}:${place.longitude}`}>
                  <div>{place.name}<span className="alert-address">{place.address || "주소 정보 없음"}</span></div>
                  {editing && draftPlaces.length > 1 && <button type="button" aria-label={`${place.name} 알림에서 제외`}
                    onClick={() => setDraftPlaces(current => current.filter(candidate => candidate !== place))}>제외</button>}
                </li>)}
              </ul>
            </dd></div>
            <div><dt>탐색 반경</dt><dd>{editing
              ? <div className="alert-radius-options" role="group" aria-label="알림 탐색 반경">{SEARCH_RADIUS_OPTIONS_METERS.map(radius =>
                <button ref={radius === DEFAULT_SEARCH_RADIUS_METERS ? radiusButtonRef : undefined} type="button" key={radius} aria-pressed={draftRadius === radius} onClick={() => setDraftRadius(radius)}>{radiusKilometersLabel(radius)}</button>)}</div>
              : radiusKilometersLabel(alertRadius)}</dd></div>
            <div><dt>알림 기간</dt><dd>등록 다음날부터 7일간<span className="alert-address">한국시간 기준</span></dd></div>
          </dl>
          {editing ? <>
            <p className="alert-help">장소 제외와 반경 변경은 알림에만 적용돼요. 알림을 받을 장소는 한 곳 이상 남겨주세요.</p>
            <div className="alert-edit-actions"><button type="button" onClick={() => finishEditing(false)}>취소</button><button type="submit">수정 완료</button></div>
          </> : <button ref={editButtonRef} className="alert-edit" type="button" onClick={() => {
            setDraftRadius(alertRadius); setDraftPlaces(alertPlaces); setEditing(true);
          }}>알림 조건 수정하기</button>}
          </form>
        </section>
        <section className="alert-registration" aria-labelledby="alert-email-title">
          <h3 id="alert-email-title">새로 등록되면 알려드릴게요</h3>
          <form onSubmit={submit}>
            <label htmlFor="alert-email">알림 받을 이메일</label>
            <input id="alert-email" type="email" required autoComplete="email" maxLength={254} placeholder="name@example.com"
              value={email} onChange={event => { setEmail(event.target.value); setNotice(""); }} aria-describedby="alert-api-note alert-email-help" />
            <p id="alert-email-help" className="alert-help">연결 후에는 메일의 확인 링크를 눌러야 알림이 시작돼요.</p>
            <div className="alert-schedule">
              <p>알림 등록 다음 날부터 7일 동안 새로 등록된 습득물을 확인합니다. 검색 조건과 비슷한 물품이 등록된 날에만 입력하신 이메일로 안내를 보내드립니다.</p>
              <p>7일째에는 새 물품이 없어도 알림 종료와 연장 방법을 안내드립니다. 연장하지 않으면 알림이 종료되며, 등록한 이메일 주소는 종료 다음 날 삭제됩니다.</p>
              <p>알림 메일 하단의 해지 링크에서 언제든 수신을 중단할 수 있습니다.</p>
            </div>
            <fieldset className="alert-consents">
              <legend>개인정보 수집·이용 동의</legend>
              <div className="alert-consent">
                <label><input type="checkbox" name="alert-consent" required /> <span>[필수] 알림을 위한 개인정보 수집·이용에 동의해요</span></label>
                <button className="alert-consent-view" type="button" aria-haspopup="dialog" aria-label="알림 개인정보 수집·이용 전문 보기" onClick={() => setConsentKey("alert")}>전문 보기</button>
              </div>
              <div className="alert-consent">
                <label><input type="checkbox" name="search-quality-consent" /> <span>[선택] 검색 품질 개선을 위한 검색어 이용에 동의해요</span></label>
                <button className="alert-consent-view" type="button" aria-haspopup="dialog" aria-label="검색 품질 개선 동의 전문 보기" onClick={() => setConsentKey("search")}>전문 보기</button>
              </div>
            </fieldset>
            <button className="alert-submit" type="submit" disabled={editing}>이 조건으로 알림 받기</button>
            <p id="alert-api-note" className="alert-preview-note">화면 미리보기 · 아직 신청과 메일 발송은 지원하지 않아요.</p>
            <p role="status" className="alert-submit-notice">{notice}</p>
          </form>
        </section>
      </div> : <div className="alert-empty"><h3>먼저 찾는 물건을 검색해주세요</h3><p>장소를 선택하고 물품을 검색하면, 그 조건을 그대로 가져올게요.</p><button className="alert-submit" type="button" onClick={onClose}>물건 찾기로 돌아가기</button></div>}
    </dialog>
    <dialog ref={consentDialogRef} className="alert-consent-dialog" aria-labelledby="alert-consent-title"
      onCancel={event => { event.preventDefault(); setConsentKey(null); }}>
      {consent && <>
        <h2 id="alert-consent-title">{consent.title}</h2>
        <dl>
          <div><dt>수집 항목</dt><dd>{consent.items}</dd></div>
          <div><dt>이용 목적</dt><dd>{consent.purpose}</dd></div>
          <div><dt>보유·이용 기간</dt><dd>{consent.retention}</dd></div>
          <div><dt>동의 거부</dt><dd>{consent.refusal}</dd></div>
        </dl>
        <button type="button" className="alert-submit" onClick={() => setConsentKey(null)}>닫기</button>
      </>}
    </dialog></div>, document.body,
  );
}
