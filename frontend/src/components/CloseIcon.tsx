// 상세·알림 창의 닫기 버튼에 쓰는 X 아이콘. 16×16 안에서 정중앙에 그린다.
export function CloseIcon() {
  return (
    <svg className="close-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3 3l10 10M13 3L3 13" />
    </svg>
  );
}
