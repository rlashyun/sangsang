import { FoundItemThumbnail } from "./FoundItemThumbnail";
import { formatRegisteredDate } from "../lib/presentation";
import type { AgentItem, Institution } from "../types";

export const AGENT_ITEM_PAGE_SIZE = 6;

export function AgentResultList({ items, institutions, onOpen }: {
  items: AgentItem[];
  institutions: Map<number, Institution>;
  onOpen: (item: AgentItem) => void;
}) {
  return (
    <ul className="agent-item-list">
      {items.map(item => <li key={item.item_id}>
        <button type="button" className="agent-item" onClick={() => onOpen(item)}>
          <FoundItemThumbnail item={item} />
          <span>
            <strong>{item.item_name || "이름 미상"}</strong>
            <small>{[item.raw_category_name, item.color_name, formatRegisteredDate(item.registered_on)].filter(Boolean).join(" · ")}</small>
            <small>보관 · {institutions.get(item.institution_id)?.name || item.raw_storage_name || "기관 정보 없음"}</small>
          </span>
        </button>
      </li>)}
    </ul>
  );
}
