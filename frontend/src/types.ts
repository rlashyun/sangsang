export interface Institution {
  storage_location_id?: number;
  location_source_code?: string;
  source_key?: string;
  name: string;
  address: string;
  phone?: string;
  organization?: string;
  source?: string;
  longitude: number;
  latitude: number;
  item_count?: number;
}

export interface Place {
  name: string;
  address: string;
  phone: string;
  category: string;
  latitude: number;
  longitude: number;
  url: string;
}

export interface KakaoPlaceDocument {
  place_name?: string;
  address_name?: string;
  road_address_name?: string;
  road_address?: { address_name?: string };
  address?: { address_name?: string };
  phone?: string;
  category_name?: string;
  place_url?: string;
  x?: string;
  y?: string;
}

export interface FoundItem {
  id?: number;
  storage_location_id?: number | null;
  item_source_code?: string;
  item_name?: string;
  raw_category_name?: string;
  color_name?: string;
  description?: string;
  image_url?: string | null;
  registered_on?: string;
  raw_storage_name?: string;
  match_score?: number;
}

export interface FoundItemsPayload {
  items?: FoundItem[];
  /** 검색어가 있을 때 — 선택 기관 범위의 전체 물품 수 */
  candidate_count?: number | null;
  /** 검색 결과 총수. 검색어 없는 목록은 첫 페이지에서만 제공하고 다음부터 null */
  total_count?: number | null;
  /** 다음 50건 커서. 검색은 search:offset, 일반 목록은 등록일:id. 마지막 페이지는 null */
  next_cursor?: string | null;
  query?: string;
  semantic_status?: "ok" | "disabled" | "unavailable" | "skipped";
  match_count?: number;
}

export interface InstitutionPayload {
  institutions?: Institution[];
  counts_available?: boolean;
  count_error?: string | null;}

export interface PlacesPayload {
  documents?: KakaoPlaceDocument[];
}

export interface SelectionSummary {
  title: string;
  description: string;
  institution?: boolean;
}
