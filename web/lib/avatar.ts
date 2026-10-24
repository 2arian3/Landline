"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RemoteAudioTrack, RemoteVideoTrack, Room } from "livekit-client";
import { api } from "./live";

/**
 * The Concierge's face (A-11): a Higgs Avatar lip-syncing the renter's call, watched in a LiveKit room.
 *
 * While the call is live the page asks POST /api/sessions/{id}/avatar (server/landline/api/avatar.py); the
 * first ask starts the avatar on the server. The answers:
 *   - 200 {url, room, identity, token}: join; the avatar is the participant `identity`, a few seconds later
 *   - 404 "off": this server has no avatars, so never ask again; 404 "no_call": ask again while it's live
 *   - 503: the start was refused (no free slot); ask again after `retry_after_s`
 * The token can only subscribe. The avatar leaves once the call is over and it has finished speaking, and
 * the page leaves the room with it. Nothing here is load-bearing: no answer, a refused start or a dropped
 * room just means no face, and the call goes on as before. LiveKit's client (~150 kB) loads only once
 * there is a face to show.
 */

type Join = { url: string; room: string; identity: string; token: string };

const ASK_AGAIN_MS = 4000;
const ARRIVE_MS = 25_000; // joined, but no avatar by then: its start failed, so ask again
const LEAVE_AFTER_MS = 30_000; // the call ended and the avatar is still there: leave anyway

export function useAvatar(sessionId: string | null, active: boolean) {
  const [video, setVideo] = useState<RemoteVideoTrack | null>(null);
  const [audio, setAudio] = useState<RemoteAudioTrack | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const [joined, setJoined] = useState(false);
  const [attempt, setAttempt] = useState(0); // bumped to ask again after the room went away
  const room = useRef<Room | null>(null);

  const leave = useCallback(() => {
    const r = room.current;
    room.current = null;
    void r?.disconnect();
    setVideo(null);
    setAudio(null);
    setSpeaking(false);
    setJoined(false);
  }, []);

  // Another session, or the page going away: out of the room.
  useEffect(() => leave, [sessionId, leave]);

  // Ask while the call is live, until in the room.
  useEffect(() => {
    if (!sessionId || !active || room.current) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const later = (ms: number) => {
      if (!stopped) timer = setTimeout(ask, ms);
    };

    async function ask() {
      const res = await fetch(api(`/api/sessions/${encodeURIComponent(sessionId!)}/avatar`), {
        method: "POST",
        signal: AbortSignal.timeout(8000),
      }).catch(() => null);
      if (stopped) return;
      if (!res?.ok) {
        const why = (res ? await res.json().catch(() => ({})) : {}) as { reason?: string; retry_after_s?: number };
        if (why.reason === "off") return;
        return later(why.retry_after_s ? why.retry_after_s * 1000 : ASK_AGAIN_MS);
      }
      const join = (await res.json()) as Join;
      const lk = await import("livekit-client");
      if (stopped) return;
      const r = new lk.Room({ adaptiveStream: true });
      const mine = (identity: string) => identity === join.identity;
      r.on(lk.RoomEvent.TrackSubscribed, (track, _pub, who) => {
        if (!mine(who.identity)) return;
        if (track.kind === lk.Track.Kind.Video) setVideo(track as RemoteVideoTrack);
        else if (track.kind === lk.Track.Kind.Audio) setAudio(track as RemoteAudioTrack);
      })
        .on(lk.RoomEvent.TrackUnsubscribed, (track, _pub, who) => {
          if (!mine(who.identity)) return;
          if (track.kind === lk.Track.Kind.Video) setVideo(null);
          else if (track.kind === lk.Track.Kind.Audio) setAudio(null);
        })
        .on(lk.RoomEvent.ActiveSpeakersChanged, (speakers) => setSpeaking(speakers.some((s) => mine(s.identity))))
        // The avatar left: the call is over, or its session broke. Out of the room; while the call is still
        // live, ask again (the server starts a new one when it can).
        .on(lk.RoomEvent.ParticipantDisconnected, (who) => {
          if (!mine(who.identity) || room.current !== r) return;
          leave();
          setAttempt((n) => n + 1);
        })
        .on(lk.RoomEvent.Disconnected, () => {
          if (room.current !== r) return;
          leave();
          setAttempt((n) => n + 1);
        });
      room.current = r;
      try {
        await r.connect(join.url, join.token, { autoSubscribe: true });
        if (room.current === r) setJoined(true);
      } catch {
        if (room.current === r) room.current = null;
        later(ASK_AGAIN_MS);
      }
    }

    void ask();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [sessionId, active, attempt, leave]);

  // In the room with no avatar after a while: its start failed. Leave and ask again (the server answers 503
  // until it can try once more).
  useEffect(() => {
    if (!joined || video) return;
    const t = setTimeout(() => {
      leave();
      setAttempt((n) => n + 1);
    }, ARRIVE_MS);
    return () => clearTimeout(t);
  }, [joined, video, leave]);

  // The call is over: the avatar finishes its last words and leaves, and the page with it. If it lingers,
  // leave anyway.
  useEffect(() => {
    if (active || !joined) return;
    const t = setTimeout(leave, LEAVE_AFTER_MS);
    return () => clearTimeout(t);
  }, [active, joined, leave]);

  /** Lets the face's audio play where the browser held it back; call it from a click. */
  const startAudio = useCallback(() => {
    void room.current?.startAudio().catch(() => {});
  }, []);

  return { video, audio, speaking, startAudio };
}
