import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { categoryIcon, formatRegisteredDate, sourceLabel } from "../lib/presentation";
import type { FoundItem, Institution } from "../types";

interface FoundItemDetailProps {
  item: FoundItem;
  institution?: Institution;
  onClose: () => void;
}

export function FoundItemDetail({ item, institution, onClose }: FoundItemDetailProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pointerStartedOutside = useRef(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  // PRD 3.2-2 — 원천의 기본 그림은 사진 없음으로 처리한다.
  const imageUrl = item.image_url && !item.image_url.split(/[?#]/)[0].endsWith("/img02_no_img.gif")
    ? item.image_url
    : null;
  const showImage = imageUrl && !imageFailed;
  const phone = institution?.phone?.trim();
  const dialNumber = phone?.replace(/[^\d+]/g, "");

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

  function isOutside(event: { clientX: number; clientY: number }) {
    const bounds = dialogRef.current?.getBoundingClientRect();
    return !!bounds && (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom);
  }

  return createPortal(
    <dialog
      className="item-detail"
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
        <h2 id="item-detail-title">물품 상세정보</h2>
        <button className="item-detail-close" type="button" onClick={onClose} autoFocus>
          닫기 <span aria-hidden="true">×</span>
        </button>
      </header>
      <div className="item-detail-content" role="region" aria-label="물품 상세 내용" tabIndex={0}>
        <div className="item-detail-photo">
          {showImage ? (
            <>
              {!imageLoaded && <p role="status">사진을 불러오고 있습니다…</p>}
              <img
                src={imageUrl}
                alt={`${item.item_name || "습득물"} 사진`}
                onLoad={() => setImageLoaded(true)}
                onError={() => setImageFailed(true)}
                hidden={!imageLoaded}
              />
            </>
          ) : (
            <div className="item-detail-photo-empty">
              <span aria-hidden="true">{categoryIcon(item.raw_category_name)}</span>
              <p>{imageFailed ? "사진을 불러오지 못했습니다." : "등록된 사진이 없습니다."}</p>
            </div>
          )}
        </div>
        <h3 className="item-detail-name">{item.item_name || "이름 미상"}</h3>
        <dl className="item-detail-fields">
          <div><dt>분류</dt><dd>{item.raw_category_name || "정보 없음"}</dd></div>
          <div><dt>색상</dt><dd>{item.color_name || "정보 없음"}</dd></div>
          <div><dt>등록일</dt><dd>{formatRegisteredDate(item.registered_on) || "정보 없음"}</dd></div>
          <div><dt>출처</dt><dd>{item.item_source_code ? sourceLabel(item.item_source_code) : "정보 없음"}</dd></div>
        </dl>
        {/* PRD 3.3 — 설명 원문과 줄바꿈을 유지하고 HTML로 해석하지 않는다. */}
        <h3>물품 설명</h3>
        <p className="item-detail-description">{item.description || "정보 없음"}</p>
        {/* PRD 4.3-C · M3 — 이름 유사성이 아닌 검증된 기관 ID로 연락처를 연결한다. */}
        <h3>보관기관</h3>
        <dl className="item-detail-fields">
          <div><dt>기관명</dt><dd>{institution?.name || item.raw_storage_name || "정보 없음"}</dd></div>
          <div><dt>주소</dt><dd>{institution?.address || "정보 없음"}</dd></div>
          <div><dt>전화번호</dt><dd>{phone && dialNumber
            ? <a href={`tel:${dialNumber}`}>{phone}</a>
            : "정보 없음"}</dd></div>
        </dl>
      </div>
    </dialog>,
    document.body,
  );
}
