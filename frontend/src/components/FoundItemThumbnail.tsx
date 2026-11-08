import { useState } from "react";

import { categoryIcon, foundItemImageUrl, sourceLabel } from "../lib/presentation";
import type { FoundItem } from "../types";

export function FoundItemThumbnail({ item }: { item: FoundItem }) {
  const imageUrl = foundItemImageUrl(item.image_url);
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const hasImage = imageUrl !== null && failedUrl !== imageUrl;
  const isLoaded = hasImage && loadedUrl === imageUrl;

  return (
    <div className="item-placeholder item-thumbnail">
      <div className="item-thumbnail-visual">
        {!isLoaded && <span aria-hidden="true">{categoryIcon(item.raw_category_name)}</span>}
        {hasImage && (
          <img
            src={imageUrl}
            alt={`${item.item_name || "습득물"} 사진`}
            // PRD 3.2-2 · 5 — 원본 사진은 보이는 목록 주변에서만 지연 로딩하고 영역을 고정한다.
            loading="lazy"
            decoding="async"
            width={88}
            height={88}
            className={isLoaded ? "is-loaded" : ""}
            onLoad={() => setLoadedUrl(imageUrl)}
            onError={() => setFailedUrl(imageUrl)}
          />
        )}
        {!hasImage && (
          <span className="sr-only">
            {imageUrl ? "사진을 불러오지 못했습니다." : "등록된 사진이 없습니다."}
          </span>
        )}
      </div>
      <small>{sourceLabel(item.item_source_code)}</small>
    </div>
  );
}
