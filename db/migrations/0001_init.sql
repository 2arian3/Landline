-- Landline schema v1. Forward-only: add a new numbered file for any change.
-- Ranked fields are typed columns; raw source fields and provenance are jsonb.

create extension if not exists postgis;
create extension if not exists vector;

-- ── reference data ──────────────────────────────────────────────────────────

create table neighbourhoods (
  id           int primary key,                         -- City of Toronto neighbourhood id
  name         text not null,
  geom         geography(MultiPolygon, 4326) not null,
  adjacent     int[] not null default '{}',
  median_rent  jsonb not null default '{}'              -- {"0": 1850, "1": 2350, "2": 3100}
);
create index neighbourhoods_geom on neighbourhoods using gist (geom);

create table stations (
  id    text primary key,                               -- TTC GTFS stop id
  name  text not null,
  line  text,
  geom  geography(Point, 4326) not null
);
create index stations_geom on stations using gist (geom);

create table buildings (                                -- City of Toronto RentSafeTO registration + evaluation
  building_id            bigserial primary key,
  rsn                    bigint unique,
  address_norm           text,
  geom                   geography(Point, 4326),
  year_built             int,
  storeys                int,
  units                  int,
  pets_allowed           text,
  pet_restrictions       text,
  ac_type                text,
  laundry                text,
  parking_type           text,
  visitor_parking        text,
  locker                 boolean,
  non_smoking            boolean,
  separate_hydro         boolean,
  elevators              int,
  barrier_free_entrance  boolean,
  mgmt_company           text,
  eval_score             numeric,
  eval_date              date,
  eval_detail            jsonb not null default '{}'
);
create index buildings_geom on buildings using gist (geom);

-- ── listings ────────────────────────────────────────────────────────────────

create table listings (
  listing_id          text primary key,                 -- '<source>:<source_id>[:<unit>]'
  source              text not null,
  source_id           text not null,
  url                 text not null,
  first_seen          timestamptz not null,
  last_seen           timestamptz not null,
  status              text not null default 'active' check (status in ('active', 'stale', 'removed')),

  address             text not null,
  unit_label          text,
  building_name       text,
  city                text not null default 'Toronto',
  postal_code         text,
  geom                geography(Point, 4326),
  geocode_precision   text not null default 'none',
  neighbourhood       text,
  neighbourhood_id    int references neighbourhoods (id),
  building_id         bigint references buildings (building_id),

  rent                int not null check (rent > 0),
  fees                jsonb not null default '[]',      -- [{name, amount, period, mandatory, quote}]
  monthly_cost        int not null,                     -- rent + mandatory monthly fees, computed in code

  beds                numeric(3, 1) not null,
  dens                int not null default 0,
  baths               numeric(3, 1),
  sqft                int,
  floor               int,
  unit_type           text,
  building_type       text,
  furnished           boolean,
  available_from      date,
  lease_months        int,

  parking             text not null default 'unknown',
  parking_cost        int,
  pets                text not null default 'unknown',
  laundry             text not null default 'unknown',
  ac                  boolean,
  dishwasher          boolean,
  outdoor_space       text,
  elevator            boolean,
  step_free           boolean,
  utilities_included  text[] not null default '{}',
  amenities           text[] not null default '{}',

  light_score         real,
  light_basis         jsonb,
  transit_station_id  text references stations (id),
  transit_walk_min    real,
  price_vs_median     real,
  red_flags           text[] not null default '{}',

  title               text,
  description         text,
  photos              jsonb not null default '[]',
  provenance          jsonb not null default '{}',      -- {field: {source, quote?, at, version}}
  embedding           vector(1536),
  raw                 jsonb not null default '{}',

  unique (source, source_id)
);
create index listings_geom on listings using gist (geom);
create index listings_rank_filters on listings (status, beds, monthly_cost);
create index listings_last_seen on listings (source, last_seen);

-- one row per pipeline run, so the refresh workflow leaves an audit trail
create table ingest_runs (
  run_id       bigserial primary key,
  source       text not null,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  status       text not null default 'running' check (status in ('running', 'ok', 'partial', 'failed')),
  pages        int not null default 0,
  fetched      int not null default 0,
  inserted     int not null default 0,
  updated      int not null default 0,
  marked_stale int not null default 0,
  error        text
);

-- ── people ──────────────────────────────────────────────────────────────────

create table users (
  user_id         bigserial primary key,
  phone_e164      text unique not null,
  name            text,
  language        text,
  consent_memory  boolean not null default false,
  created_at      timestamptz not null default now(),
  last_seen_at    timestamptz
);

create table user_profiles (
  user_id         bigint primary key references users on delete cascade,
  hard            jsonb not null default '{}',          -- {max_monthly, min_beds, pets[], must_have[]}
  priorities      jsonb not null default '[]',          -- [{feature, weight}], most recent first
  anchors         jsonb not null default '[]',          -- [{label, lat, lng}]
  move_in         date,
  occupants       int,
  shareable       text[] not null default '{move_in,occupants,pets}',
  memory_summary  text,
  updated_at      timestamptz not null default now()
);

create table listing_interactions (
  user_id     bigint references users on delete cascade,
  listing_id  text references listings,
  session_id  text,
  action      text not null,                            -- viewed | liked | rejected | called | booked
  reason      text,
  attribute   text,                                     -- light | location | size | cost | parking ...
  at          timestamptz not null default now()
);
create index listing_interactions_user on listing_interactions (user_id, at desc);

-- ── runtime ─────────────────────────────────────────────────────────────────

create table sessions (
  session_id  text primary key,
  user_id     bigint references users,
  channel     text not null check (channel in ('phone', 'browser', 'judge')),
  status      text not null default 'open',
  version     int not null default 0,
  snapshot    jsonb not null default '{}',
  started_at  timestamptz not null default now(),
  ended_at    timestamptz
);

create table session_listings (
  session_id       text references sessions on delete cascade,
  listing_id       text references listings,
  rank             int,
  score            real,
  contributions    jsonb,
  status           text not null default 'pending',
  reaction         text,
  reaction_reason  text,
  updated_at       timestamptz not null default now(),
  primary key (session_id, listing_id)
);

create table calls (
  call_id      text primary key,
  session_id   text references sessions,
  listing_id   text references listings,
  role         text not null check (role in ('renter', 'listing')),
  transport    text not null check (transport in ('voice', 'sim', 'browser', 'stub')),
  twilio_sid   text,
  state        text not null,
  end_reason   text,
  created_at   timestamptz not null default now(),
  answered_at  timestamptz,
  ended_at     timestamptz,
  deadline_at  timestamptz,
  attempt      int not null default 1
);

create table call_turns (
  call_id  text references calls on delete cascade,
  idx      int,
  speaker  text not null check (speaker in ('renter', 'landlord', 'agent')),
  text     text not null,
  lang     text,
  t_ms     int,
  primary key (call_id, idx)
);

create table facts (
  fact_id     bigserial primary key,
  call_id     text references calls,
  listing_id  text references listings,
  field       text not null,
  value       jsonb not null,
  quote       text not null,
  turn_idx    int,
  confirmed   boolean not null default false,
  source      text not null check (source in ('tool', 'transcript', 'both')),
  created_at  timestamptz not null default now()
);

create table board_messages (
  msg_id      text primary key,
  session_id  text references sessions on delete cascade,
  kind        text not null check (kind in ('ask_all', 'need_answer', 'reply', 'fact')),
  from_role   text not null check (from_role in ('renter', 'listing')),
  call_id     text,
  listing_id  text,
  text        text not null,
  reply_to    text,
  status      text not null default 'open' check (status in ('open', 'answered', 'expired')),
  created_at  timestamptz not null default now(),
  ttl_s       int not null default 25
);

create table events (
  seq         bigserial primary key,
  session_id  text,
  call_id     text,
  type        text not null,
  idem_key    text unique,
  payload     jsonb not null,
  at          timestamptz not null default now()
);
create index events_session on events (session_id, seq);
