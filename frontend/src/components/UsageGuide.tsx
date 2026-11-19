import { useEffect, useRef, type MouseEvent } from "react";
import { Link } from "react-router-dom";

// PRD 2.2, 4.4 — 2026-11-17 전체 물품 확대. 경찰청 분류 전체를 다루며, 아래는 자주 찾는 예시다.
const categories = ["지갑", "카드", "휴대폰", "가방", "의류", "전자기기", "현금", "신분증·면허증", "귀금속", "안경"];

type UsageGuideProps = {
  searchPath: string;
  surveyUrl: string;
  onDismiss: () => void;
  // chat: 대화형 검색 화면용 안내, classic: 기존 검색 화면용 안내
  variant?: "chat" | "classic";
};

function isBackdropClick(event: MouseEvent<HTMLDialogElement>) {
  const { currentTarget, target, clientX, clientY } = event;
  if (target !== currentTarget) return false;
  const { left, top, right, bottom } = currentTarget.getBoundingClientRect();
  return clientX < left || clientX > right || clientY < top || clientY > bottom;
}

// PRD 4.4 — 검색 화면 위에서 서비스 소개와 두 탭의 이용 방법을 안내한다.
export function UsageGuide({ searchPath, surveyUrl, onDismiss, variant = "classic" }: UsageGuideProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const pointerStartedOutside = useRef(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    titleRef.current?.focus({ preventScroll: true });
    return () => dialog?.close();
  }, []);

  return (
    <dialog ref={dialogRef} className="usage-guide" aria-labelledby="usage-guide-title"
      onCancel={event => { event.preventDefault(); onDismiss(); }}
      onPointerDown={event => { pointerStartedOutside.current = isBackdropClick(event); }}
      onClick={event => {
        if (pointerStartedOutside.current && isBackdropClick(event)) onDismiss();
        pointerStartedOutside.current = false;
      }}>
      <header className="usage-guide-header">
        <h1 id="usage-guide-title" ref={titleRef} tabIndex={-1}>리트리버 이용안내</h1>
        <button className="usage-guide-close" type="button" onClick={onDismiss} aria-label="이용안내 닫기">
          <span aria-hidden="true">×</span>
        </button>
      </header>
      <div className="usage-guide-content">
        <p className="usage-guide-intro">잃어버린 장소에서부터 찾아보세요. 주변 보관기관과 그곳에 등록된 습득물을 한눈에 볼 수 있어요.</p>

        <div className="usage-guide-steps">
          {variant === "chat" ? (
          <section className="usage-guide-card" aria-labelledby="usage-find-title">
            <h2 id="usage-find-title">물건 찾기</h2>
            <ol>
              <li>대화창에 물건을 잃어버린 장소를 말해 주세요. 지도에서 보관기관 핀을 두 번 눌러 고를 수도 있어요.</li>
              <li>후보 장소 중 맞는 곳을 확인해요. 틀리면 ‘장소 변경’으로 다시 고를 수 있어요.</li>
              <li>‘검은색 지갑’처럼 잃어버린 물건의 이름이나 특징을 말해 주세요.</li>
              <li>결과 목록에서 물품을 눌러 상세 정보를 보고, 필요하면 검색 반경을 넓혀요.</li>
            </ol>
            <aside className="usage-guide-note">
              <strong>방문 전 보관기관에 연락해요.</strong>
              <p>상세 화면에 연락처와 수령 방법이 있어요.</p>
            </aside>
          </section>
          ) : (
          <section className="usage-guide-card" aria-labelledby="usage-find-title">
            <h2 id="usage-find-title">물건 찾기</h2>
            <ol>
              <li>장소명이나 주소를 입력해 물건을 잃어버린 장소를 최대 3곳까지 선택해요.</li>
              <li>‘검은색 지갑’처럼 이름이나 특징을 검색해요.</li>
              <li>결과가 없으면 반경을 1km → 1.5km → 2km로 넓혀요.</li>
              <li>그래도 못 찾았다면 결과 아래 ‘비슷한 물건 알림 받기’를 눌러요.</li>
            </ol>
            <aside className="usage-guide-note">
              <strong>방문 전 보관기관에 연락해요.</strong>
              <p>상세 화면에 연락처와 수령 방법이 있어요.</p>
            </aside>
          </section>
          )}

          <section className="usage-guide-card" aria-labelledby="usage-alert-title">
            <h2 id="usage-alert-title">비슷한 물건 알림</h2>
            <ol>
              <li>{variant === "chat" ? "검색 결과 위의" : "검색 결과에서"} ‘비슷한 물건 알림 받기’를 눌러요.</li>
              <li>검색 조건을 확인하고 이메일을 입력해요.</li>
              <li>확인 메일의 링크를 눌러 알림을 시작해요.</li>
            </ol>
            <aside className="usage-guide-note">
              <strong>등록 다음 날부터 7일간 알림</strong>
              <p>비슷한 새 물품이 등록되면 메일을 보내요. 종료 메일에서 연장할 수 있어요.</p>
            </aside>
          </section>
        </div>

        <section className="usage-guide-card usage-guide-categories" aria-labelledby="usage-categories-title">
          <h2 id="usage-categories-title">찾을 수 있는 물품</h2>
          <p>경찰청에 등록되는 습득물은 종류와 상관없이 모두 찾을 수 있어요. 자주 찾는 물품은 이런 것들이에요.</p>
          <ul>
            {categories.map(category => <li key={category}>{category}</li>)}
          </ul>
        </section>

        <div className="usage-guide-actions">
          <section className="usage-guide-action" aria-labelledby="usage-search-title">
            <h2 id="usage-search-title">이제 잃어버린 물건을 찾아볼까요?</h2>
            <p>{variant === "chat" ? "잃어버린 장소를 말하는 것부터 시작해 보세요." : "잃어버린 장소를 검색하는 것부터 시작해 보세요."}</p>
            <Link className="usage-guide-button" to={searchPath} state={{ fromGuideSearch: true }}>물건 찾으러 가기 <span aria-hidden="true">→</span></Link>
          </section>
          <section className="usage-guide-action" aria-labelledby="usage-survey-title">
            <h2 id="usage-survey-title">리트리버를 도와주세요</h2>
            <p>이용하면서 불편했던 점이나 추가됐으면 하는 물품을 알려주세요.</p>
            <a className="usage-guide-button" href={surveyUrl} target="_blank" rel="noopener noreferrer">
              설문조사 참여하기 <span aria-hidden="true">↗</span>
            </a>
          </section>
        </div>
      </div>
    </dialog>
  );
}
