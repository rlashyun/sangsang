import { useEffect, useRef, useState } from "react";

import { fetchFoundItems } from "../api/client";
import { ensureBufferedItemPage, itemPageNumbers } from "../lib/item-pagination.js";
import type { AgentItem, FoundItem, Institution } from "../types";
import { AgentResultList } from "./AgentResultList";

function toAgentItems(items: FoundItem[]): AgentItem[] {
  return items.flatMap(item => item.id != null && item.storage_location_id != null
    ? [{ ...item, item_id: item.id, institution_id: item.storage_location_id }] : []);
}

export function AgentInstitutionResults({ ids, query, institutions, onOpen, onVisibleItems, onTotalChange, pageSize }: {
  pageSize: number;
  ids: string;
  query: string;
  institutions: Map<number, Institution>;
  onOpen: (item: AgentItem) => void;
  onTotalChange: (count: number | null) => void;
  onVisibleItems: (ids: number[]) => void;
}) {
  const [items, setItems] = useState<FoundItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const pageCount = Math.ceil((nextCursor ? total : items.length) / pageSize);

  useEffect(() => {
    const request = new AbortController();
    active.current = request;
    fetchFoundItems(new URLSearchParams({ location_ids: ids, q: query }), request.signal).then(payload => {
      setItems(payload.items ?? []);
      setNextCursor(payload.next_cursor ?? null);
      setTotal(payload.total_count ?? 0);
      onTotalChange(payload.total_count ?? payload.items?.length ?? 0);
    }).catch(err => {
      if (!request.signal.aborted) setError(err.message);
    }).finally(() => {
      if (!request.signal.aborted) {
        setLoading(false);
        active.current = null;
      }
    });
    return () => active.current?.abort();
  }, [ids, query, onTotalChange]);

  useEffect(() => {
    const pageItems = items.slice(page * pageSize, (page + 1) * pageSize);
    onVisibleItems(toAgentItems(pageItems).map(item => item.item_id));
  }, [items, page, pageSize, onVisibleItems]);

  useEffect(() => { setPage(0); }, [pageSize]);

  async function changePage(target: number) {
    if (active.current) return;
    const request = new AbortController();
    active.current = request;
    setLoading(true);
    setError("");
    try {
      const buffer = await ensureBufferedItemPage({
        items, nextCursor, page: target, pageSize: pageSize,
        isCurrent: () => !request.signal.aborted,
        loadNext: cursor => fetchFoundItems(new URLSearchParams({ location_ids: ids, q: query, cursor }), request.signal),
      });
      if (buffer) {
        setItems(buffer.items);
        setNextCursor(buffer.nextCursor);
        setPage(buffer.page);
      }
    } catch (err) {
      if (!request.signal.aborted) setError(err instanceof Error ? err.message : "기관 결과를 가져오지 못했어요.");
    } finally {
      if (!request.signal.aborted) {
        active.current = null;
        setLoading(false);
      }
    }
  }

  return <>
    {loading && <p className="agent-bubble" role="status">이 기관의 결과를 확인하고 있어요.</p>}
    {error && <p className="agent-bubble" role="alert">{error}</p>}
    {!loading && !error && !items.length && <p className="agent-bubble">이 기관에는 일치하는 습득물이 없어요.</p>}
    <AgentResultList pageSize={pageSize} items={toAgentItems(items.slice(page * pageSize, (page + 1) * pageSize))} institutions={institutions} onOpen={onOpen} />
    <nav className="agent-pagination" aria-label="기관 습득물 결과 페이지">
      {pageCount <= 1 ? <button type="button" aria-label="1페이지" aria-current="page" disabled={loading}>1</button> : itemPageNumbers(page, pageCount).map((index: number) => <button type="button" key={index}
        aria-current={index === page ? "page" : undefined} disabled={loading}
        onClick={() => void changePage(index)}>{index + 1}</button>)}
    </nav>
  </>;
}
