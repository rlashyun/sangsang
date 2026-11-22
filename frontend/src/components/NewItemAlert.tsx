import { useEffect, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { createAlertReservation } from "../api/client";
import { DEFAULT_SEARCH_RADIUS_METERS, radiusKilometersLabel, SEARCH_RADIUS_OPTIONS_METERS } from "../lib/geo.js";
import type { Place } from "../types";
import { CloseIcon } from "./CloseIcon";

export const CONSENT_DETAILS = {
  title: "알림을 위한 개인정보 수집·이용",
  items: "이메일 주소, 알림에 포함한 장소 이름과 좌표, 탐색 반경, 물품 검색어",
  purpose: "이메일 주소 확인, 저장한 조건으로 새 유사 습득물 검색, 알림과 종료·연장 안내 발송, 서비스 이용 통계",
  retention: "이메일 주소는 알림 종료 다음 날 삭제해요. 연장하면 연장된 종료일 다음 날에, 취소하면 즉시, 확인하지 않으면 신청 24시간 뒤에 삭제해요. 이메일 삭제 후에도 검색어·장소·반경은 이메일과 연결되지 않은 상태로 남겨 서비스 이용 통계에 사용해요.",
  refusal: "동의를 거부할 수 있어요. 동의하지 않아도 물건 찾기는 이용할 수 있고, 이메일 알림은 신청할 수 없어요.",
};

type Submission = { status: "idle" | "submitting" | "sent" } | { status: "error"; message: string };

interface NewItemAlertProps {
  places: readonly Place[];
  query: string;
  radiusMeters: number;
  onClose: () => void;
}

// PRD 4.3-E — 검색 조건을 전달하고 메일 확인을 안내한다.
export function NewItemAlert({ places, query, radiusMeters, onClose }: NewItemAlertProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const consentDialogRef = useRef<HTMLDialogElement>(null);
  const sentHeadingRef = useRef<HTMLHeadingElement>(null);
  const requestPending = useRef(false);
  const pointerOutside = useRef(false);
  const [email, setEmail] = useState("");
  const [submission, setSubmission] = useState<Submission>({ status: "idle" });
  const isSubmitting = submission.status === "submitting";
  const isSent = submission.status === "sent";
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
  useEffect(() => { if (isSent) sentHeadingRef.current?.focus(); }, [isSent]);

  function finishEditing(apply: boolean) {
    if (apply) {
      setAlertRadius(draftRadius);
      setAlertPlaces(draftPlaces);
      setSubmission({ status: "idle" });
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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!hasConditions || requestPending.current || editing || isSent) return;
    if (!new FormData(event.currentTarget).has("alert-consent")) return;

    requestPending.current = true;
    setSubmission({ status: "submitting" });
    try {
      await createAlertReservation({
        email: email.trim(),
        query,
        places: alertPlaces.map(({ name, latitude, longitude }) => ({ name, latitude, longitude })),
        radius_m: alertRadius,
        consent: true,
      });
      setSubmission({ status: "sent" });
    } catch (error) {
      setSubmission({
        status: "error",
        message: error instanceof Error && !(error instanceof TypeError)
          ? error.message
          : "서버에 연결하지 못했어요. 확인 메일이 도착했는지 확인하고, 메일이 없다면 다시 시도해 주세요.",
      });
    } finally {
      requestPending.current = false;
    }
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
        <div><p className="alert-eyebrow">알림 조건 확인</p><h2 id="new-item-alert-title">비슷한 물건 알림</h2></div>
        <button type="button" className="item-detail-close" aria-label="닫기" onClick={onClose} autoFocus><CloseIcon /></button>
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
          </> : !isSent && <button ref={editButtonRef} className="alert-edit" type="button" disabled={isSubmitting} onClick={() => {
            setDraftRadius(alertRadius); setDraftPlaces(alertPlaces); setEditing(true);
          }}>알림 조건 수정하기</button>}
          </form>
        </section>
        <section className="alert-registration" aria-labelledby="alert-email-title">
          <h3 id="alert-email-title" ref={sentHeadingRef} tabIndex={isSent ? -1 : undefined}>
            {isSent ? "확인 메일을 보냈어요" : "새로 등록되면 알려드릴게요"}
          </h3>
          {isSent ? <div className="alert-confirmation">
            <p><strong>24시간 안에</strong> 메일의 확인 링크를 눌러야 알림이 시작돼요.</p>
            <p>메일이 보이지 않으면 <strong>스팸함</strong>이나 광고함에서 ‘리트리버’를 찾아보세요.</p>
            <button className="alert-submit" type="button" onClick={onClose}>확인했어요</button>
          </div> : <form onSubmit={submit}>
            <label htmlFor="alert-email">알림 받을 이메일</label>
            <input id="alert-email" type="email" required autoComplete="email" maxLength={254} placeholder="name@example.com"
              value={email} disabled={isSubmitting} onChange={event => { setEmail(event.target.value); setSubmission({ status: "idle" }); }} aria-describedby="alert-email-help" />
            <p id="alert-email-help" className="alert-help">메일의 확인 링크를 눌러야 알림이 시작돼요.</p>
            <div className="alert-schedule">
              <p>알림 등록 다음 날부터 7일 동안 새로 등록된 습득물을 확인합니다. 검색 조건과 비슷한 물품이 등록된 날에만 입력하신 이메일로 안내를 보내드립니다.</p>
              <p>7일째에는 새 물품이 없어도 알림 종료와 연장 방법을 안내드립니다. 연장하지 않으면 알림이 종료되며, 등록한 이메일 주소는 종료 다음 날 삭제됩니다.</p>
              <p>알림 메일 하단의 해지 링크에서 언제든 수신을 중단할 수 있습니다.</p>
            </div>
            <fieldset className="alert-consents" disabled={isSubmitting}>
              <legend>개인정보 수집·이용 동의</legend>
              <div className="alert-consent">
                <label><input type="checkbox" name="alert-consent" required /> <span>[필수] 알림을 위한 개인정보 수집·이용에 동의해요</span></label>
                <button className="alert-consent-view" type="button" aria-haspopup="dialog" aria-label="알림 개인정보 수집·이용 전문 보기" onClick={() => consentDialogRef.current?.showModal()}>전문 보기</button>
              </div>
            </fieldset>
            <button className="alert-submit" type="submit" disabled={editing || isSubmitting}>
              {isSubmitting ? "확인 메일 보내는 중…" : "이 조건으로 알림 받기"}
            </button>
            {submission.status === "error" && <p role="alert" className="alert-submit-error">{submission.message}</p>}
          </form>}
        </section>
      </div> : <div className="alert-empty"><h3>먼저 찾는 물건을 검색해주세요</h3><p>장소를 선택하고 물품을 검색하면, 그 조건을 그대로 가져올게요.</p><button className="alert-submit" type="button" onClick={onClose}>물건 찾기로 돌아가기</button></div>}
    </dialog>
    <dialog ref={consentDialogRef} className="alert-consent-dialog" aria-labelledby="alert-consent-title">
        <header className="alert-consent-header">
          <h2 id="alert-consent-title">{CONSENT_DETAILS.title}</h2>
          <button type="button" className="item-detail-close" aria-label="닫기" onClick={() => consentDialogRef.current?.close()}><span aria-hidden="true">×</span></button>
        </header>
        <dl>
          <div><dt>수집 항목</dt><dd>{CONSENT_DETAILS.items}</dd></div>
          <div><dt>이용 목적</dt><dd>{CONSENT_DETAILS.purpose}</dd></div>
          <div><dt>보유·이용 기간</dt><dd>{CONSENT_DETAILS.retention}</dd></div>
          <div><dt>동의 거부</dt><dd>{CONSENT_DETAILS.refusal}</dd></div>
        </dl>
    </dialog></div>, document.body,
  );
}
