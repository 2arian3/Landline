// Mirror of server/landline/contracts. Keep in sync; shape drift shows up as blank cards.

export type CardStatus = "pending" | "calling" | "verified" | "suspect" | "dead" | "no_answer" | "booked";

export type Fact = { field: string; value: string; quote: string; speaker: string; at?: string | null };

export type Card = {
  listing_id: string;
  address: string;
  unit_label?: string | null;
  neighbourhood?: string | null;
  lat?: number | null;
  lng?: number | null;
  rent: number;
  monthly_cost: number;
  beds: number;
  baths?: number | null;
  sqft?: number | null;
  floor?: number | null;
  unit_type?: string | null;
  photo?: string | null;
  source: string;
  url: string;
  rank: number;
  status: CardStatus;
  reasons: string[];
  watch_out?: string | null;
  reaction?: string | null;
  facts: Fact[];
  red_flags: string[];
};

export type Preferences = {
  max_monthly?: number | null;
  min_beds?: number | null;
  areas: string[];
  pets: string[];
  must_have: string[];
  priorities: string[];
};

export type SessionView = {
  session_id: string;
  version: number;
  user_name?: string | null;
  returning: boolean;
  preferences: Preferences;
  focus_listing_id?: string | null;
  matches: string[];
  board: Card[];          // ranked; never sort this in the page
  agent_says: string;
};

export type CataloguePoint = { listing_id: string; lat: number; lng: number; monthly_cost: number; beds: number };

export type Provenance = { source: string; quote?: string | null; at?: string | null; version?: string | null };

export type ListingDetail = {
  listing_id: string;
  source: string;
  url: string;
  address: string;
  building_name?: string | null;
  postal_code?: string | null;
  lat?: number | null;
  lng?: number | null;
  rent: number;
  monthly_cost: number;
  beds: number;
  baths?: number | null;
  sqft?: number | null;
  floor?: number | null;
  unit_type?: string | null;
  building_type?: string | null;
  furnished?: boolean | null;
  parking: string;
  pets: string;
  laundry: string;
  utilities_included: string[];
  amenities: string[];
  photos: string[];
  last_seen: string;
  provenance: Record<string, Provenance>;
};
