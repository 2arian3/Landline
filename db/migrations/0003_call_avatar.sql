-- Higgs Avatar clips, after a call: one MP4 of Landline's own voice (the agent track).
-- Additive. The call is unchanged if a render never starts or fails.

create table call_avatar (
  call_id         text primary key references calls on delete cascade,
  status          text not null check (status in ('queued', 'rendering', 'ready', 'failed', 'skipped')),
  boson_video_id  text,
  error           text,
  video           bytea,
  updated_at      timestamptz not null default now()
);
