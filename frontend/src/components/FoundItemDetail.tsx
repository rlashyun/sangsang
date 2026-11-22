import { useEffect, useRef, useState, type Ref } from "react";
import { createPortal } from "react-dom";

import { formatRegisteredDate, foundItemImageUrl, sourceLabel } from "../lib/presentation";
import type { FoundItem, Institution } from "../types";
import { ItemPickupGuide } from "./ItemPickupGuide";
import { CategoryIcon } from "./CategoryIcon";
import { CloseIcon } from "./CloseIcon";

interface FoundItemDetailProps {
  item: FoundItem;
  institution?: Institution;
  onClose: () => void;
}

function DetailPhoto({ imageUrl, name, category, onPhotoOpen }: { imageUrl: string | null; name: string; category?: string; onPhotoOpen?: () => void }) {
  const [attempt, setAttempt] = useState(0);
  const currentAttempt = useRef(0);
  const photoRef = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  function retry() {
    // PRD 3.1-9 · 4.3-C — 원본 URL을 보존하고 사용자가 요청한 경우에만 다시 시도한다.
    currentAttempt.current += 1;
    setAttempt(currentAttempt.current);
    setLoaded(false);
    setFailed(false);
    photoRef.current?.focus({ preventScroll: true });
  }

  return <div ref={photoRef} tabIndex={-1} className="item-detail-photo" aria-label="물품 사진">
    {imageUrl && !failed ? <>
      {!loaded && <div className="item-detail-photo-status">
        {/* PRD 4.3-C · 5 — 시간 경과와 무관하게 기본 로딩을 유지하고 실제 실패 때만 재시도한다. */}
        <p role="status">사진을 불러오고 있습니다…</p>
      </div>}
      <div className="item-detail-image" aria-busy={!loaded}>
        {onPhotoOpen && loaded && <button type="button" className="item-detail-photo-open" aria-label={`${name} 사진 크게 보기`} onClick={onPhotoOpen} />}
        <img key={attempt} src={imageUrl} alt={`${name} 사진`} width={320} height={340}
        onLoad={() => { if (attempt === currentAttempt.current) setLoaded(true); }}
        onError={() => { if (attempt === currentAttempt.current) setFailed(true); }} hidden={!loaded} />
      </div>
    </> : <div className="item-detail-photo-empty">
      <CategoryIcon category={category} />
      <p role="status">{failed ? "사진을 불러오지 못했습니다." : "등록된 사진이 없습니다."}</p>
      {failed && <button type="button" onClick={retry}>사진 다시 불러오기</button>}
    </div>}
  </div>;
}

export function FoundItemDetail({ item, institution, onClose }: FoundItemDetailProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pointerStartedOutside = useRef(false);
  const [showPickup, setShowPickup] = useState(false);
  const pickupTriggerRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const detailScrollRef = useRef(0);
  const returningToDetail = useRef(false);
  const phone = institution?.phone?.trim();

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    dialog?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      dialog?.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus({ preventScroll: true });
      }
    };
  }, []);

  useEffect(() => {
    if (showPickup || !returningToDetail.current) return;
    if (contentRef.current) contentRef.current.scrollTop = detailScrollRef.current;
    pickupTriggerRef.current?.focus({ preventScroll: true });
    returningToDetail.current = false;
  }, [showPickup]);

  function isOutside(event: { clientX: number; clientY: number }) {
    const bounds = dialogRef.current?.getBoundingClientRect();
    return !!bounds && (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom);
  }

  return createPortal(
    <dialog
      className={`item-detail${showPickup ? " pickup-view" : ""}`}
      ref={dialogRef}
      aria-labelledby="item-detail-title"
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = event.currentTarget.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], [tabindex="0"]',
        );
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
      onPointerDown={(event) => { pointerStartedOutside.current = isOutside(event); }}
      onClick={(event) => {
        if (event.target === event.currentTarget && pointerStartedOutside.current && isOutside(event)) {
          onClose();
        }
        pointerStartedOutside.current = false;
      }}
    >
      <header className="item-detail-header">
        <h2 id="item-detail-title">{showPickup ? "물품 수령 안내" : "물품 상세정보"}</h2>
        <button className="item-detail-close" type="button" aria-label="닫기" onClick={onClose} autoFocus>
          <CloseIcon />
        </button>
      </header>
      {showPickup ? <ItemPickupGuide
        itemName={item.item_name || "이름 미상"}
        institutionName={institution?.name || item.raw_storage_name || "보관기관 정보 없음"}
        address={institution?.address}
        phone={phone}
        onBack={() => { returningToDetail.current = true; setShowPickup(false); }}
        onClose={onClose}
      /> : <>
      <FoundItemInformation item={item} institution={institution} contentRef={contentRef} />
      <footer className="item-match-confirmation">
        <p>찾으시는 물건이 맞나요?</p>
        <div className="item-match-actions">
          <button className="item-match-no" type="button" onClick={onClose}>아니요, 더 찾아볼게요</button>
          <button className="item-match-yes" ref={pickupTriggerRef} type="button" onClick={() => {
            detailScrollRef.current = contentRef.current?.scrollTop ?? 0;
            setShowPickup(true);
          }}>예, 수령 방법 보기</button>
        </div>
      </footer>
      </>}
    </dialog>,
    document.body,
  );
}

// PRD 4.3-C — 대화형 상세와 기존 상세창은 같은 원문·기관 정보를 표시한다.
export function FoundItemInformation({ item, institution, contentRef, onPhotoOpen }: {item: FoundItem; institution?: Institution; contentRef?: Ref<HTMLDivElement>; onPhotoOpen?: () => void}) {
  const imageUrl = foundItemImageUrl(item.image_url);
  const phone = institution?.phone?.trim();
  const dialNumber = phone?.replace(/[^\d+]/g, "");
  return (
      <div ref={contentRef} className="item-detail-content" role="region" aria-label="물품 상세 내용" tabIndex={0}>
        {/* PRD 4.3-C · 4.4 — 넓은 화면에서는 요약과 본문을 병렬 배치한다. */}
        <div className="item-detail-overview">
          <DetailPhoto key={imageUrl} imageUrl={imageUrl} name={item.item_name || "습득물"} category={item.raw_category_name} onPhotoOpen={onPhotoOpen} />
          {item.item_source_code && <p className="item-detail-source">등록 정보 출처 · {sourceLabel(item.item_source_code)}</p>}
        </div>
        <div className="item-detail-body">
          <div className="item-detail-summary">
          <h3 className="item-detail-name">{item.item_name || "이름 미상"}</h3>
          <dl className="item-detail-fields item-detail-basics">
            <div><dt>분류</dt><dd>{item.raw_category_name || "정보 없음"}</dd></div>
            <div><dt>색상</dt><dd>{item.color_name || "정보 없음"}</dd></div>
            <div><dt>등록일</dt><dd>{formatRegisteredDate(item.registered_on) || "정보 없음"}</dd></div>
          </dl>
          </div>
          {/* PRD 4.3-C · M3 — 이름 유사성이 아닌 검증된 기관 ID로 연락처를 연결한다. */}
          <section className="item-detail-section" aria-labelledby="item-institution-title">
          <h3 id="item-institution-title">보관기관</h3>
          <dl className="item-detail-fields">
            <div><dt>기관명</dt><dd>{institution?.name || item.raw_storage_name || "정보 없음"}</dd></div>
            <div><dt>주소</dt><dd>{institution?.address || "정보 없음"}</dd></div>
            <div className="item-detail-contact-row"><dt>전화번호</dt><dd>{phone && dialNumber
              ? <a href={`tel:${dialNumber}`}>{phone}</a>
              : "정보 없음"}</dd></div>
          </dl>
          </section>
        </div>
      </div>
  );
}
