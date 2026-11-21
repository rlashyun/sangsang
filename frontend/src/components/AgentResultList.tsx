import { FoundItemThumbnail } from "./FoundItemThumbnail";
import { formatRegisteredDate } from "../lib/presentation";
import type { AgentItem, Institution } from "../types";

export const AGENT_ITEM_PAGE_SIZE = 6;

export function AgentResultList({ items, institutions, onOpen, pageSize }: {
  pageSize: number;
  items: AgentItem[];
  institutions: Map<number, Institution>;
  onOpen: (item: AgentItem) => void;
}) {
  return (
    <ul className={`agent-item-list${items.length < pageSize ? " is-sparse" : ""}`}>
      {items.map(item => <li key={item.item_id}>
        <button type="button" className="agent-item" onClick={() => onOpen(item)}>
          <FoundItemThumbnail item={item} />
          <span className="agent-item-copy">
            <strong className="agent-item-name" title={item.item_name || "이름 미상"}>{item.item_name || "이름 미상"}</strong>
            {/* PRD 4.3-C — 목록에는 구체적인 하위분류만, 원문 전체 분류는 상세에 유지한다. */}
            <span className="agent-item-attributes">
              {item.raw_category_name?.trim() && <span className="agent-item-category" title={item.raw_category_name}>
                {item.raw_category_name.split(">").map(part => part.trim()).filter(Boolean).at(-1)}
              </span>}
              {item.color_name?.trim() && <span className="agent-item-color">{item.color_name}</span>}
            </span>
            <small className="agent-item-meta">
              <span className="agent-item-storage">{institutions.get(item.institution_id)?.name || item.raw_storage_name || "기관 정보 없음"} 보관</span>
              <span aria-hidden="true">·</span>
              <span className="agent-item-date">{item.registered_on ? `${formatRegisteredDate(item.registered_on)} 등록` : "등록일 정보 없음"}</span>
            </small>
          </span>
        </button>
      </li>)}
    </ul>
  );
}
