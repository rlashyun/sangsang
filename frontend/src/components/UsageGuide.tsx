import { useEffect, useRef, type MouseEvent } from "react";
import { Link } from "react-router-dom";

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
        <p className="usage-guide-intro">잃어버린 장소에서부터 찾아보세요. 주변 보관기관에 등록된 습득물을 종류와 상관없이 찾아볼 수 있어요.</p>

        <div className="usage-guide-steps">
          {variant === "chat" ? (
          <section className="usage-guide-card" aria-labelledby="usage-find-title">
            <h2 id="usage-find-title">물건 찾기</h2>
            <ol>
              <li>대화창에 물건을 잃어버린 장소를 말해 주세요. 지도에서 보관기관 핀을 두 번 눌러 고를 수도 있어요.</li>
              <li>후보 장소를 확인하고 ‘장소선택’을 눌러요. 다른 장소도 대화로 알려주면 최대 3곳까지 선택할 수 있어요.</li>
              <li>‘검은색 반지갑’, ‘빨간 케이스를 낀 삼성 휴대폰’처럼 물건의 이름이나 특징을 알려주세요. 사진을 첨부하거나 붙여넣을 수도 있어요.</li>
              <li>일치하는 물건이 없으면 1km → 1.5km → 2km까지 자동으로 넓혀 찾아요.</li>
              <li>결과 목록에서 물품을 눌러 보관 위치와 상세 정보를 확인해요.</li>
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
              <li>{variant === "chat" ? "검색 후 대화창의 선택지에서" : "검색 결과에서"} ‘비슷한 물건 알림 받기’를 눌러요.</li>
              <li>검색 조건을 확인하고 이메일을 입력해요.</li>
              <li>확인 메일의 링크를 눌러 알림을 시작해요.</li>
            </ol>
            <aside className="usage-guide-note">
              <strong>등록 다음 날부터 7일간 알림</strong>
              <p>비슷한 새 물품이 등록되면 메일을 보내요. 종료 메일에서 연장할 수 있어요.</p>
            </aside>
            {variant === "chat" && <section className="usage-guide-daangn" aria-labelledby="usage-daangn-title">
              <h2 id="usage-daangn-title">당근에서 검색하기</h2>
              <ol>
                <li>검색 후 대화창에서 ‘당근에서 찾아보기’를 눌러요.</li>
                <li>선택한 장소와 물건이 검색어에 자동으로 입력된 당근 검색 화면이 새 탭으로 열려요.</li>
              </ol>
            </section>}
          </section>
        </div>

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
