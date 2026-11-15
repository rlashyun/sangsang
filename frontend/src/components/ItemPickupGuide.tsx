import { useEffect, useRef } from "react";

interface ItemPickupGuideProps {
  itemName: string;
  institutionName: string;
  address?: string;
  phone?: string;
  onBack: () => void;
  onClose: () => void;
}

// PRD 4.3-C · M3 — 기관 방문 안내이며 수령 신청이나 소유 확인 완료를 처리하지 않는다.
export function ItemPickupGuide({ itemName, institutionName, address, phone, onBack, onClose }: ItemPickupGuideProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const dialNumber = phone?.replace(/[^\d+]/g, "");
  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); }, []);
  return <>
    <div className="item-detail-content pickup-guide-content" role="region" aria-label="물품 수령 안내 내용" tabIndex={0}>
      <div className="pickup-guide-intro">
        <p className="pickup-kicker">방문 전 확인해주세요</p>
        <h3 ref={headingRef} tabIndex={-1}>이렇게 수령할 수 있어요</h3>
        <p className="pickup-item-name">{itemName}</p>
      </div>
      <div className="pickup-guide-columns">
        <section className="pickup-contact" aria-label="수령 문의 기관">
          <h4>보관기관에 먼저 연락해주세요</h4>
          <strong>{institutionName}</strong>
          <dl>
            <div><dt>주소</dt><dd>{address || "주소 정보가 없어요. 기관에 위치를 확인해주세요."}</dd></div>
            <div className="pickup-contact-phone"><dt>전화번호</dt><dd>{phone && dialNumber
              ? <a href={`tel:${dialNumber}`}>{phone}</a>
              : "연락처 정보가 없어요. 해당 기관의 공식 안내에서 확인해주세요."}</dd></div>
          </dl>
          <p>물품이 다른 기관으로 옮겨졌을 수 있어요. 방문 전에 현재 보관 장소와 방문 가능 시간을 확인해주세요.</p>
        </section>
        <ol className="pickup-steps" aria-label="수령 순서">
          <li><span aria-hidden="true">1</span><div><h4>보관 여부와 방문 시간 확인</h4><p>물품명과 등록일을 알려주고 현재 보관 중인지, 어디로 방문하면 되는지 물어보세요.</p></div></li>
          <li><span aria-hidden="true">2</span><div><h4>신분증과 물품 특징 준비</h4><p>본인을 확인할 수 있는 신분증을 준비하고, 색상·브랜드·내용물 등 내 물건임을 설명할 정보를 확인해주세요.</p></div></li>
          <li><span aria-hidden="true">3</span><div><h4>안내받은 장소에서 수령</h4><p>보관기관을 방문해 담당자의 안내에 따라 본인과 물품을 확인한 뒤 수령하세요.</p></div></li>
        </ol>
      </div>
      <p className="pickup-note">대리 수령이나 신분증 분실 시 필요한 준비물은 방문 전에 기관에 확인해주세요.</p>
      <a className="pickup-source" href="https://www.korea.kr/multi/visualNewsView.do?newsId=148913220" target="_blank" rel="noopener noreferrer">공식 수령 안내 참고 ↗</a>
    </div>
    <footer className="pickup-guide-actions">
      <button className="pickup-back" type="button" onClick={onBack}>물품 정보 다시 보기</button>
      <button className="pickup-done" type="button" onClick={onClose}>확인했어요</button>
    </footer>
  </>;
}
