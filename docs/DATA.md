# Data: sources, pipeline, features and database

Owner: Arian. Reviewed by Danial (extraction quality) and Mahyar (what the UI shows).

## Database decision

**PostgreSQL 16 on Supabase (region ca-central-1) with PostGIS and pgvector.** The ranking hot path reads from an in-memory feature matrix, not from the database.

| Need | Why this fits |
|---|---|
| Three people building against the same data from day one | One hosted URL; nothing to tunnel into |
| Geographic features (distance to work, neighbourhood polygons, nearest station, joining listings to City buildings) | PostGIS: `ST_DWithin`, `ST_Contains`, `ST_Distance` on `geography(Point)` with GiST indexes |
| Structured filters (rent, beds, pets, parking) plus flexible per-source fields | Typed columns for anything we rank on; `jsonb` for raw source fields and provenance |
| Renter profiles, sessions, calls, facts, an append-only event log | Transactions, foreign keys, `bigserial` event sequence |
| "Bright, quiet, near a park" style wishes (stretch) | pgvector on description embeddings, used as one more weighted term |
| Speed during a call | Listings (~1–2k rows) load into memory at boot; ranking is a pure function; writes are small transactions |

We considered SQLite and dropped it: three machines writing to one file doesn't work, and it has no geo. A vector-only database was also dropped: the facts that decide a rental are structured, or come from phone calls, not from similarity. Local development uses the shared Supabase project. Each developer can create their own schema to experiment in.

## Schema (first migration, `db/migrations/0001_init.sql`)

Ranked fields are columns; everything else goes in `jsonb`. Every listing field the renter sees has an entry in `provenance`.

```sql
create extension if not exists postgis;
create extension if not exists vector;

-- reference data
create table neighbourhoods (id int primary key, name text not null, geom geography(MultiPolygon) not null,
  adjacent int[] not null default '{}', median_rent jsonb not null default '{}');      -- {"1": 2350, "2": 3100}
create table stations (id text primary key, name text not null, line text, geom geography(Point) not null);
create table buildings (building_id bigserial primary key, rsn bigint unique, address_norm text,
  geom geography(Point), year_built int, storeys int, units int,
  pets_allowed text, pet_restrictions text, ac_type text, laundry text, parking_type text,
  visitor_parking text, locker boolean, non_smoking boolean, separate_hydro boolean, elevators int,
  barrier_free_entrance boolean, mgmt_company text, eval_score numeric, eval_date date,
  eval_detail jsonb not null default '{}');                                            -- City per-area scores, e.g. pests

-- listings
create table listings (
  listing_id text primary key,                         -- '<source>:<source_id>[:<unit>]'
  source text not null, source_id text not null, url text not null,
  first_seen timestamptz not null, last_seen timestamptz not null,
  status text not null default 'active',               -- active | stale | removed
  address text not null, unit_label text, postal_code text,
  geom geography(Point), geocode_precision text,       -- rooftop | street | postal | none
  neighbourhood_id int references neighbourhoods(id), building_id bigint references buildings(building_id),
  rent int not null, fees jsonb not null default '[]', -- [{name, amount, period, mandatory, quote}]
  monthly_cost int not null,                           -- rent + mandatory monthly fees, computed in code
  beds numeric(3,1) not null, dens int not null default 0, baths numeric(3,1),
  sqft int, floor int, unit_type text,                 -- apartment | condo | house | basement | room | townhouse
  furnished boolean, available_from date, lease_months int,
  parking text, parking_cost int,                      -- included | extra | none | unknown
  pets text,                                           -- any | cats_only | dogs_only | small_only | none | case_by_case | unknown
  laundry text, ac boolean, dishwasher boolean, outdoor_space text, elevator boolean, step_free boolean,
  utilities_included text[] not null default '{}',
  light_score real, light_basis jsonb,                 -- {floor, cues[], photo_luma}
  transit_station_id text references stations(id), transit_walk_min real,
  price_vs_median real,                                -- monthly_cost / neighbourhood median for same beds
  red_flags text[] not null default '{}',
  title text, description text, photos jsonb not null default '[]',
  provenance jsonb not null default '{}',              -- {field: {source, quote?, at, version}}
  embedding vector(1536),                              -- stretch: description embedding
  raw jsonb not null default '{}'
);
create index on listings using gist (geom);
create index on listings (status, beds, monthly_cost);

-- people
create table users (user_id bigserial primary key, phone_e164 text unique not null, name text,
  language text, consent_memory boolean not null default false,
  created_at timestamptz not null default now(), last_seen_at timestamptz);
create table user_profiles (user_id bigint primary key references users on delete cascade,
  hard jsonb not null default '{}',                    -- {max_monthly, min_beds, pets[], must_have[]}
  priorities jsonb not null default '[]',              -- [{feature, weight}] ordered
  anchors jsonb not null default '[]',                 -- [{label, lat, lng}]
  move_in date, occupants int, shareable text[] not null default '{move_in,occupants,pets}',
  memory_summary text, updated_at timestamptz not null default now());
create table listing_interactions (user_id bigint references users on delete cascade, listing_id text references listings,
  session_id text, action text not null, reason text, attribute text, at timestamptz not null default now());

-- runtime
create table sessions (session_id text primary key, user_id bigint references users, channel text not null,
  status text not null, version int not null default 0, snapshot jsonb not null default '{}',
  started_at timestamptz not null default now(), ended_at timestamptz);
create table session_listings (session_id text references sessions on delete cascade, listing_id text references listings,
  rank int, score real, contributions jsonb, status text not null default 'pending', reaction text, reaction_reason text,
  updated_at timestamptz not null default now(), primary key (session_id, listing_id));
create table calls (call_id text primary key, session_id text references sessions, listing_id text references listings,
  role text not null, transport text not null, twilio_sid text, state text not null, end_reason text,
  created_at timestamptz not null default now(), answered_at timestamptz, ended_at timestamptz, deadline_at timestamptz,
  attempt int not null default 1);
create table call_turns (call_id text references calls on delete cascade, idx int, speaker text not null,
  text text not null, lang text, t_ms int, primary key (call_id, idx));
create table facts (fact_id bigserial primary key, call_id text references calls, listing_id text references listings,
  field text not null, value jsonb not null, quote text not null, turn_idx int, confirmed boolean not null default false,
  source text not null, created_at timestamptz not null default now());
create table board_messages (msg_id text primary key, session_id text references sessions on delete cascade,
  kind text not null, from_role text not null, call_id text, listing_id text, text text not null,
  reply_to text, status text not null, created_at timestamptz not null default now(), ttl_s int not null default 25);
create table events (seq bigserial primary key, session_id text, call_id text, type text not null,
  idem_key text unique, payload jsonb not null, at timestamptz not null default now());
```

## Pipeline

Each stage reads the previous stage's JSONL from `data/stages/<stage>/` and writes its own, so any stage can be re-run alone. Raw responses are kept under `data/raw/<source>/<date>/` (gitignored, shared as a release asset if needed).

| # | Stage | Does | Done when |
|---|---|---|---|
| 1 | `fetch` | Downloads listing pages or JSON politely (≤1 request every 2 s, identified User-Agent, cached). Uses the site's own structured data first: the JSON its map view loads, JSON-LD (`schema.org` Apartment with `geo`), or `__NEXT_DATA__`. Uses Playwright only when a page needs JavaScript | ≥ 800 Toronto listings snapshotted |
| 2 | `parse` | Turns raw responses into rows; expands building price/bed ranges into explicit unit rows with unit labels | Every row has address, rent, beds, url |
| 3 | `geocode` | Uses map-pin coordinates if present, otherwise geocodes the address (cached). Records precision | ≥ 90% at street precision or better |
| 4 | `enrich` | Neighbourhood by point-in-polygon; nearest TTC station and walk minutes; City building match; neighbourhood median rent | Coverage report shows each join rate |
| 5 | `extract` | LLM reads title + description and returns typed features, each with a verbatim quote. The shared grounding check drops any field whose quote isn't in the text | Spot check of 30 rows: no ungrounded values |
| 6 | `light` | Light score from floor, text cues and photo brightness (see SCORING.md) | Every row has a score and its basis |
| 7 | `dedupe` | Same unit across sources or reposts: address + unit + 25 m + rent/beds similarity | Duplicate rate reported |
| 8 | `load` | Upsert into Postgres; mark rows not seen in this run `stale` | `make data-report` prints counts per field |

**Rules for scraping.**
- Check each source's `robots.txt` and terms before fetching. Skip anything behind a login.
- Never store listing agents' or landlords' phone numbers or emails. Every call goes to `DEMO_AGENT_PHONES`.
- Link to the source listing; don't re-host photos in the public repo.
- Keep the snapshot frozen for the demo. The README says when it was taken.

**City of Toronto open data** (Open Government Licence – Toronto), verified through the City's CKAN API on 21 Oct 2026:
- **Apartment Building Registration**: 3,611 buildings, refreshed 2026-07-05. Columns we use: `RSN`, `SITE_ADDRESS`, `PCODE`, `YEAR_BUILT`, `PETS_ALLOWED`, `PET_RESTRICTIONS`, `AIR_CONDITIONING_TYPE`, `LAUNDRY_ROOM`, `LAUNDRY_ROOM_LOCATION`, `PARKING_TYPE`, `VISITOR_PARKING`, `LOCKER_OR_STORAGE_ROOM`, `NON_SMOKING_BUILDING`, `SEPARATE_HYDRO_METER_EACH_UNIT`, `NO_OF_ELEVATORS`, `BARRIER_FREE_ACCESSIBILTY_ENTR` (the City's spelling), `PROP_MANAGEMENT_COMPANY_NAME`.
- **Apartment Building Evaluation (2023–current)**: 6,902 rows, refreshed 2026-10-08. Columns we use: `RSN`, `LATITUDE`, `LONGITUDE`, `CURRENT BUILDING EVAL SCORE`, `COMMON AREA PESTS`, `BUILDING CLEANLINESS`, `ELEVATOR MAINTENANCE`, `EVALUATION COMPLETED ON`.
- **Matching a listing to a building:** take the nearest evaluation point within 30 m whose street number matches, then join the registration by `RSN`. Coverage is RentSafeTO's scope (larger purpose-built rental buildings), so condos and houses won't match. That's expected; for them the field is `unknown`.
- Also used: City neighbourhood boundaries (158 neighbourhoods) and TTC GTFS stops (subway stations).

## Feature dictionary

Every feature the ranker or the card uses. "Sources" lists them in order of trust; a phone call overrides the listing. Whenever the City record and the listing disagree, the disagreement becomes a question for the Caller.

| Feature | Type / unit | Sources | Notes |
|---|---|---|---|
| `geom` (lat, lng) | point | map pin › geocoder | precision kept; `postal`-only rows are shown but not ranked on distance |
| `neighbourhood_id` | City id | PostGIS | adjacency from shared borders |
| `distance_to_anchor_km` | km, per anchor | PostGIS / haversine | anchors come from the profile ("work at King and Bay") |
| `transit_walk_min` | minutes | nearest subway station | haversine × 1.3 ÷ 80 m/min |
| `monthly_cost` | CAD | rent + mandatory monthly fees | computed in code, never by a model |
| `price_vs_median` | ratio | neighbourhood median for same beds | < 0.7 adds a "too good to be true" check |
| `beds`, `dens`, `baths` | number | listing | building ranges expanded into units |
| `sqft` | ft² | listing › LLM | |
| `floor` | int (0 = ground, -1 = basement) | listing › LLM ("14th floor", "lower level") | |
| `unit_type` | enum | listing › LLM | `basement` caps the light score |
| `light_score` | 0–1 | floor + text cues + photo brightness | see SCORING.md; always shown as "estimated" |
| `parking`, `parking_cost` | enum, CAD | call › LLM › City `PARKING_TYPE` | |
| `pets` | enum | call › LLM › City `PETS_ALLOWED` / `PET_RESTRICTIONS` | compared to the renter's species |
| `laundry` | in_suite / shared / none | LLM › City `LAUNDRY_ROOM` | |
| `ac` | bool | LLM › City `AIR_CONDITIONING_TYPE` | |
| `utilities_included` | list | call › LLM › City separate meters (hint only) | |
| `elevator`, `step_free` | bool | City › LLM | accessibility filter |
| `outdoor_space` | balcony / terrace / yard / none | LLM | |
| `furnished`, `available_from`, `lease_months` | | listing › call | move-in fit |
| `building_eval_score`, `pests_flag` | 0–100, bool | City evaluation | shown with the evaluation date |
| `year_built` | int | City | |
| `verified` | pending / verified / suspect / dead | calls | verified facts beat listing claims |
| `red_flags` | list | LLM on text + Auditor on calls + price check | deposit before viewing, e-transfer only, no viewings, owner abroad, pressure |
| `embedding` | vector(1536) | description | stretch: "vibe" similarity |
