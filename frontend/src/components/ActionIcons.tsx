// 결과 아래 버튼 앞의 아이콘 — 알림(종)과 당근 검색(돋보기). 바깥 링크 화살표(↗) 대신 쓴다.
export function BellIcon() {
  return (
    <svg className="action-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M4 11V7.5a4 4 0 0 1 8 0V11l1 1.5H3z" />
      <path d="M6.5 14h3" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg className="action-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="7" cy="7" r="4" />
      <path d="m10 10 3.5 3.5" />
    </svg>
  );
}
