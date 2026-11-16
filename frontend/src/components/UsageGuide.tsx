import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import { CloseIcon } from "./CloseIcon";

// 지원하는 6개 분류 — AGENTS.md 용어표·PRD 4.5 (이 밖의 습득물은 수집하지 않는다).
const CATEGORIES = ["가방", "의류(모자)", "전자기기(무선이어폰)", "지갑", "휴대폰", "카드"];

// PRD 4.4 · ui-patterns 7 — 서비스 소개, 두 기능의 사용 순서, 지원 분류와 기관 사전 연락을 한 창에 짧게 안내한다.
export function UsageGuide({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pointerStartedOutside = useRef(false);

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
      className="item-detail usage-guide"
      ref={dialogRef}
      aria-labelledby="usage-guide-title"
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
        if (event.target === event.currentTarget && pointerStartedOutside.current && isOutside(event)) onClose();
        pointerStartedOutside.current = false;
      }}
    >
      <header className="item-detail-header">
        <h2 id="usage-guide-title">이용안내</h2>
        <button className="item-detail-close" type="button" aria-label="닫기" onClick={onClose} autoFocus>
          <CloseIcon />
        </button>
      </header>
      <div className="item-detail-content usage-guide-content" role="region" aria-label="이용안내 내용" tabIndex={0}>
        <p className="usage-guide-lead">
          경찰청 습득물 데이터를 매일 모아, 잃어버린 장소 가까이의 보관기관과 그곳에서 지금 보관 중인 습득물을 지도에서 바로 찾을 수 있게 도와줘요.
        </p>
        <div className="usage-guide-columns">
          <section aria-labelledby="usage-find">
            <h3 id="usage-find">물건 찾기</h3>
            <ol>
              <li><span aria-hidden="true">1</span><p>잃어버린 장소와 물건을 대화로 알려주세요.</p></li>
              <li><span aria-hidden="true">2</span><p>지도에 표시된 장소 후보 중 맞는 곳을 골라 확정해요.</p></li>
              <li><span aria-hidden="true">3</span><p>반경 1km 안 보관기관의 습득물을 목록으로 보여줘요. 기관이 없으면 최대 2km까지 넓히고, 결과를 본 뒤에는 1km, 1.5km, 2km로 직접 바꿀 수 있어요.</p></li>
              <li><span aria-hidden="true">4</span><p>물품을 눌러 사진과 설명을 확인하고, 내 물건 같으면 수령 안내를 확인해요.</p></li>
            </ol>
          </section>
          <section aria-labelledby="usage-alert">
            <h3 id="usage-alert">새 물건 알림</h3>
            <ol>
              <li><span aria-hidden="true">1</span><p>지금 찾은 장소·반경·검색어를 확인해요.</p></li>
              <li><span aria-hidden="true">2</span><p>이메일을 입력하면 확인 메일이 가요. 메일의 링크를 눌러야 알림이 시작돼요.</p></li>
              <li><span aria-hidden="true">3</span><p>등록 다음날부터 7일 동안, 유사한 물품이 새로 등록된 날에만 메일로 알려드려요. 마지막 날에는 종료와 연장 안내를 보내요.</p></li>
            </ol>
          </section>
        </div>
        <section className="usage-guide-categories" aria-labelledby="usage-categories">
          <h3 id="usage-categories">찾을 수 있는 물품</h3>
          <ul>{CATEGORIES.map(name => <li key={name}>{name}</li>)}</ul>
        </section>
        <p className="usage-guide-note">
          수령하기 전에 보관기관에 먼저 연락해 현재 보관 중인지와 방문 시간을 확인해주세요. 리트리버는 분실 신고를 받거나 습득자와 직접 연결해 주지 않아요.
        </p>
      </div>
      <footer className="pickup-guide-actions usage-guide-actions">
        <button className="pickup-done" type="button" onClick={onClose}>확인했어요</button>
      </footer>
    </dialog>,
    document.body,
  );
}
