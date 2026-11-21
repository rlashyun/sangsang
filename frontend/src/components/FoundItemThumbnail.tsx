import { useState } from "react";

import { foundItemImageUrl } from "../lib/presentation";
import type { FoundItem } from "../types";
import { CategoryIcon } from "./CategoryIcon";

export function FoundItemThumbnail({ item }: { item: FoundItem }) {
  const originalUrl = foundItemImageUrl(item.image_url);
  const thumbnailUrl = originalUrl ? foundItemImageUrl(item.thumbnail_url) : null;
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const [failedThumbnail, setFailedThumbnail] = useState<string | null>(null);
  const imageUrl = thumbnailUrl && failedThumbnail !== thumbnailUrl ? thumbnailUrl : originalUrl;
  const hasImage = imageUrl !== null && failedUrl !== imageUrl;
  const isLoaded = hasImage && loadedUrl === imageUrl;
  const isLoading = hasImage && !isLoaded;

  return (
    <div className="item-placeholder item-thumbnail">
      <div className={`item-thumbnail-visual${isLoading ? " item-thumbnail-loading" : ""}`} aria-busy={isLoading}>
        {!hasImage && <CategoryIcon category={item.raw_category_name} />}
        {hasImage && (
          <img
            src={imageUrl}
            alt={`${item.item_name || "습득물"} 사진`}
            // PRD 3.2-2 · 5 — 축소본 실패 시 원본을 사용하고 지연 로딩과 고정 영역을 유지한다.
            loading="lazy"
            decoding="async"
            width={88}
            height={88}
            className={isLoaded ? "is-loaded" : ""}
            onLoad={() => setLoadedUrl(imageUrl)}
            onError={() => {
              if (thumbnailUrl && imageUrl === thumbnailUrl && imageUrl !== originalUrl) {
                setFailedThumbnail(thumbnailUrl);
              } else {
                setFailedUrl(imageUrl);
              }
            }}
          />
        )}
        {!hasImage && (
          <span className="sr-only">
            {imageUrl ? "사진을 불러오지 못했습니다." : "등록된 사진이 없습니다."}
          </span>
        )}
      </div>
    </div>
  );
}
