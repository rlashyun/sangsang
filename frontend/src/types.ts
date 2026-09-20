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
  item_source_code?: string;
  item_name?: string;
  raw_category_name?: string;
  color_name?: string;
  description?: string;
  registered_on?: string;
  raw_storage_name?: string;
  match_score?: number;
}

export interface FoundItemsPayload {
  items?: FoundItem[];
  candidate_count: number;
  query?: string;
}

export interface InstitutionPayload {
  institutions?: Institution[];
  counts_available?: boolean;
  count_error?: string | null;
}

export interface PlacesPayload {
  documents?: KakaoPlaceDocument[];
}

export interface SelectionSummary {
  title: string;
  description: string;
  institution?: boolean;
}
