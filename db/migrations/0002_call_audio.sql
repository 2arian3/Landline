-- Call audio (A-10): both sides of each call, and where each transcript line sits in it. Additive only.
--
-- Every call has one clock, in milliseconds from when it connected. The caller's track is the audio sent
-- to Higgs, back to back from 0, which is the clock Higgs's own audio_start_ms / audio_end_ms count on.
-- The agent's track is what the caller heard: Higgs's audio placed where it played, cut at a barge-in.
-- Audio is stored in chunks as it arrives (a few seconds each), so a crash loses only the last few.

create table call_audio (
  call_id   text references calls on delete cascade,
  track     text not null check (track in ('renter', 'landlord', 'agent')),   -- whose voice: call_turns.speaker
  seq       int not null,                                  -- order within the track, from 0
  start_ms  int not null,                                  -- where the chunk starts on the call's clock
  format    text not null check (format in ('pcmu', 'pcm16')),   -- G.711 μ-law, or PCM16 little-endian
  rate      int not null,                                  -- samples a second: 8000 phone, 24000 browser
  audio     bytea not null,                                -- mono, as it went over the wire
  primary key (call_id, track, seq)
);

-- Where each line is on that clock: from audio_start_ms to audio_end_ms of its speaker's track.
-- Null when not known (a call stored before this, or a text-only simulation).
alter table call_turns add column audio_start_ms int, add column audio_end_ms int;
