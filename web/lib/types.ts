// Mirror of server/landline/contracts. Keep in sync; shape drift shows up as blank cards.

export type CardStatus = "pending" | "calling" | "verified" | "suspect" | "dead" | "no_answer" | "booked";

// open (the brief is live) → calling → done, or closed when the brief ends with no plan.
export type Phase = "open" | "calling" | "done" | "closed";

export type CallState = "queued" | "blocked" | "dialing" | "ringing" | "live" | "voicemail" | "ended";

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
  light_score?: number | null;   // 0–1, always shown as estimated
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

export type Anchor = { label: string; lat?: number | null; lng?: number | null };

export type Area = { label: string; lat: number; lng: number; zoom: number };

export type Preferences = {
  max_monthly?: number | null;
  min_beds?: number | null;
  areas: string[];
  anchors: Anchor[];
  pets: string[];
  must_have: string[];
  priorities: string[];
  move_in?: string | null;
  occupants?: number | null;
  language?: string | null;
};

export type CallView = { call_id: string; listing_id: string; state: CallState; started_at?: string | null };

export type SessionView = {
  session_id: string;
  version: number;
  phase: Phase;
  user_name?: string | null;
  returning: boolean;
  preferences: Preferences;
  focus_listing_id?: string | null;
  focus_area?: Area | null;
  matches: string[];
  board: Card[];          // ranked; never sort this in the page
  agent_says: string;
  plan: string[];         // listing ids the renter picked to call, in order
  calls: CallView[];
  results?: string | null;  // the results text, once the phase is done
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
