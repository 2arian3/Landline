-- Demo day (X-04): every column and table the demo-day tasks use, in one migration. Additive; nothing
-- fills or reads them yet: each task does that.

alter table users add column email text;                                 -- U-12 (the renter types it in), O-08 (invites go to it)
alter table users add column use_saved_search boolean not null default false;   -- U-12d
alter table users add column push_token text;                            -- U-13c
alter table calls add column to_e164 text;                               -- A-13 (callbacks), O-08b (where the confirm link goes)
alter table listings add column grocery_name text, add column grocery_walk_min real,   -- D-10b, D-10d
  add column pharmacy_name text, add column pharmacy_walk_min real;

-- O-08: a booked viewing waiting for the listing agent to confirm it from a texted link.
create table viewing_confirmations (
  token             text primary key,
  call_id           text not null unique references calls,
  session_id        text not null references sessions,
  listing_id        text not null references listings,
  starts_at         timestamptz not null,
  status            text not null default 'pending' check (status in ('pending', 'confirmed', 'declined')),
  agent_email       text,
  calendar_event_id text,
  created_at        timestamptz not null default now(),
  decided_at        timestamptz
);
