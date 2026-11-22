import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FoundItem, Institution } from "../types";
import { FoundItemInformation } from "./FoundItemDetail";
import { ItemPickupGuide } from "./ItemPickupGuide";
import { SURVEY_URL } from "../lib/feedback";
import { foundItemImageUrl } from "../lib/presentation";

function EnlargedPhoto({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement;
    dialog.current?.showModal();
    return () => {
      dialog.current?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);
  return <dialog className="agent-photo-lightbox" ref={dialog} aria-label="물품 사진 확대"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <button type="button" onClick={onClose} autoFocus>사진 닫기</button>
    <img src={url} alt={`${name} 확대 사진`} />
  </dialog>;
}

// PRD 4.3-A · 4.3-C — 미응답 상세만 교체하고, 응답한 상세·안내는 기록에 남긴다.
export function AgentItemDetail({ item, institution, onBack, onAccept, onReject, answer, acknowledged = false, onAcknowledge }: {
  item: FoundItem; institution?: Institution; onBack?: () => void; onAccept?: () => void; onReject?: () => void;
  answer?: "yes" | "no"; acknowledged?: boolean; onAcknowledge?: () => void;
}) {
  const [zoom, setZoom] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const url = foundItemImageUrl(item.image_url);
  useLayoutEffect(() => {
    if (answer !== "yes") return;
    const card = scroll.current?.querySelector(".agent-pickup-card");
    const viewport = scroll.current?.closest<HTMLElement>(".agent-feed");
    const previous = card?.previousElementSibling;
    const height = card && previous ? card.getBoundingClientRect().bottom - previous.getBoundingClientRect().top : Infinity;
    (height <= (viewport?.clientHeight ?? 0) ? previous : card)?.scrollIntoView({ block: "start" });
  }, [answer]);
  useEffect(() => {
    if (!onBack) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || zoom || document.querySelector("dialog[open]")) return;
      event.preventDefault();
      onBack();
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onBack, zoom]);
  return <section className={`agent-detail-view agent-chat-detail${answer ? " is-recorded" : ""}`}
    data-item-id={item.id} data-answer={answer} aria-label={answer ? `${item.item_name || "물품"} 상세 대화` : "선택한 물품 상세"}>
    <div className="agent-detail-scroll" ref={scroll}>
      <div className="agent-bubble agent-bubble-agent agent-detail-card">
        <FoundItemInformation item={item} institution={institution} onPhotoOpen={url ? () => setZoom(true) : undefined} />
      </div>
      <p className="agent-bubble agent-bubble-agent">찾으시는 물건이 맞나요?</p>
      {answer ? <p className="agent-bubble agent-bubble-user">{answer === "yes" ? "예" : "아니요"}</p> :
        <div className="agent-detail-answers">
          <button type="button" className="agent-action-chip" onClick={onAccept}>예</button>
          <button type="button" className="agent-action-chip" onClick={onReject}>아니요</button>
        </div>}
      {answer === "yes" && <div className="agent-bubble agent-bubble-agent agent-pickup-card">
        <ItemPickupGuide compact confirmed={acknowledged} itemName={item.item_name || "이름 미상"}
          institutionName={institution?.name || item.raw_storage_name || "보관기관 정보 없음"}
          address={institution?.address} phone={institution?.phone}
          onBack={() => scroll.current?.querySelector(".agent-detail-card")?.scrollIntoView({ block: "start" })}
          onClose={() => onAcknowledge?.()} />
      </div>}
      {answer === "no" && <p className="agent-bubble agent-bubble-agent">다른 물건도 살펴보세요. 찾는 물건이 없으면 아래 선택지를 이용해 보세요.</p>}
      {acknowledged && <>
        <p className="agent-bubble agent-bubble-user">확인했어요</p>
        <p className="agent-bubble agent-bubble-agent">물건을 꼭 되찾으시길 바라요. 리트리버가 도움이 되었다면 짧은 설문으로 의견을 남겨주세요.</p>
        <a className="agent-action-chip agent-survey-reply" href={SURVEY_URL} target="_blank" rel="noopener noreferrer">설문조사 참여하기</a>
      </>}
    </div>
    {zoom && url && <EnlargedPhoto url={url} name={item.item_name || "물품"} onClose={() => setZoom(false)} />}
  </section>;
}
