import type { ReactElement } from "react";

import { categoryKind, type CategoryKind } from "../lib/presentation";

// 사진이 없는 습득물에 보여주는 분류별 기본 이미지. 색은 테마 토큰(styles.css .category-icon)을 따른다.
const SHAPES: Record<CategoryKind, ReactElement> = {
  bag: <>
    <path className="ci-o" d="M23 27v-5a9 9 0 0 1 18 0v5" />
    <path className="ci-f" d="M15 27h34l3.5 22.5a4 4 0 0 1-4 4.5H15.5a4 4 0 0 1-4-4.5z" />
    <path className="ci-o" d="M13 37h38" />
    <circle className="ci-d" cx="32" cy="37" r="3" />
  </>,
  cap: <>
    <path className="ci-f" d="M15 40v-3a17 17 0 0 1 34 0v3z" />
    <path className="ci-f" d="M12 40h38c5 0 7 2.5 6 4.5-1 2-4 2.5-8 2.5H14a2 2 0 0 1-2-2z" />
    <path className="ci-o" d="M32 20v20" />
    <circle className="ci-d" cx="32" cy="19" r="2.6" />
  </>,
  buds: <>
    <circle className="ci-f" cx="22" cy="26" r="8.5" />
    <path className="ci-f" d="M15 31v16a3.5 3.5 0 0 0 7 0V33" />
    <circle className="ci-d" cx="22" cy="26" r="3" />
    <circle className="ci-f" cx="42" cy="26" r="8.5" />
    <path className="ci-f" d="M49 31v16a3.5 3.5 0 0 1-7 0V33" />
    <circle className="ci-d" cx="42" cy="26" r="3" />
  </>,
  wallet: <>
    <path className="ci-o" d="M15 21l26-9 3 9" />
    <rect className="ci-f" x="9" y="21" width="46" height="32" rx="6" />
    <rect className="ci-f" x="40" y="31" width="18" height="13" rx="4" />
    <circle className="ci-d" cx="46.5" cy="37.5" r="2.4" />
  </>,
  phone: <>
    <rect className="ci-f" x="19" y="7" width="26" height="50" rx="6" />
    <path className="ci-o" d="M28 13h8" />
    <path className="ci-o" d="M28 51h8" />
  </>,
  card: <>
    <rect className="ci-f" x="7" y="16" width="50" height="33" rx="5" />
    <rect className="ci-d" x="7" y="23" width="50" height="7" />
    <rect className="ci-o" x="14" y="36" width="10" height="7" rx="1.8" />
    <path className="ci-o" d="M30 40h14" />
  </>,
  box: <>
    <path className="ci-f" d="M32 8l21 10v27L32 56 11 45V18z" />
    <path className="ci-o" d="M11 18l21 10 21-10" />
    <path className="ci-o" d="M32 28v28" />
    <path className="ci-o" d="M21.5 13l21 10" />
  </>,
};

export function CategoryIcon({ category }: { category?: string }) {
  return (
    <svg className="category-icon" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      {SHAPES[categoryKind(category)]}
    </svg>
  );
}
