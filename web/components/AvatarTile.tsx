"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { useAvatar } from "@/lib/avatar";

/** Judge mode's own voice (lib/judgeAudio.ts): whether it's playing, and a way to hush it. */
export type VoiceHandoff = { talking: boolean; quiet: (on: boolean) => void };

/**
 * The Concierge's face in the corner of the board, like the other side of a video call (A-11): a Higgs
 * Avatar lip-syncing what Landline says on the renter's call. It shows only once the avatar's video
 * arrives, and fades out when the call is over.
 *
 * On a phone call's board the renter already hears Landline on the phone, so the face is silent until
 * someone turns the sound on (a teammate watching the board, say). In judge mode (`voice`) the face takes
 * over the voice: at the first quiet moment the browser socket's audio is hushed and the face's own audio,
 * in step with its lips, plays instead; if the face goes, the socket's voice comes back.
 */
export function AvatarTile({
  sessionId,
  active,
  voice,
}: {
  sessionId: string | null;
  active: boolean;
  voice?: VoiceHandoff;
}) {
  const { video, audio, speaking, startAudio } = useAvatar(sessionId, active);
  const [soundOn, setSoundOn] = useState(false);
  const [handed, setHanded] = useState(false);
  const videoEl = useRef<HTMLVideoElement>(null);
  const audioEl = useRef<HTMLAudioElement>(null);
  const still = useReducedMotion();

  useEffect(() => {
    const el = videoEl.current;
    if (!video || !el) return;
    video.attach(el);
    return () => {
      video.detach(el);
    };
  }, [video]);

  useEffect(() => {
    const el = audioEl.current;
    if (!audio || !el) return;
    audio.attach(el);
    return () => {
      audio.detach(el);
    };
  }, [audio]);

  // Judge mode: hand the voice over between sentences, never in the middle of one, so nothing is heard twice
  // or cut off; hand it back the moment the face's audio goes.
  const talking = voice?.talking ?? false;
  const quiet = voice?.quiet;
  useEffect(() => {
    if (!quiet) return;
    if (!audio) {
      if (handed) {
        setHanded(false);
        quiet(false);
      }
      return;
    }
    if (!handed && !talking && !speaking) {
      setHanded(true);
      quiet(true);
    }
  }, [quiet, audio, handed, talking, speaking]);

  // Leaving with the voice handed over gives it back.
  const handedRef = useRef(false);
  const quietRef = useRef(quiet);
  useEffect(() => {
    handedRef.current = handed;
    quietRef.current = quiet;
  });
  useEffect(
    () => () => {
      if (handedRef.current) quietRef.current?.(false);
    },
    [],
  );

  const audible = quiet ? handed : soundOn;
  useEffect(() => {
    if (audioEl.current) audioEl.current.muted = !audible;
  }, [audible, audio]);

  function toggleSound() {
    startAudio();
    setSoundOn((on) => !on);
  }

  return (
    <>
      <audio ref={audioEl} autoPlay muted hidden />
      <AnimatePresence>
        {video && (
          <motion.figure
            key="face"
            initial={still ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={still ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.26, ease: [0.23, 1, 0.32, 1] }}
            className="@container overflow-hidden rounded-[var(--radius-card)] bg-surface"
            style={{ boxShadow: "0 0 0 1px var(--color-line), 0 4px 16px rgba(26,24,21,.10)" }}
          >
            <video
              ref={videoEl}
              autoPlay
              playsInline
              muted
              aria-label="Landline, live on the call"
              className="block aspect-[3/4] w-full bg-map-land object-cover"
            />
            <figcaption className="flex items-center justify-between gap-1 py-1 pl-2 pr-1 @[150px]:py-1.5 @[150px]:pl-3 @[150px]:pr-1.5">
              <span className="flex min-w-0 items-center gap-1.5 text-meta font-label text-ink">
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 shrink-0 rounded-full bg-live ${speaking ? "live-dot" : ""}`}
                />
                <span className="truncate">Landline</span>
              </span>
              {!quiet && (
                <button
                  type="button"
                  onClick={toggleSound}
                  aria-pressed={soundOn}
                  aria-label={soundOn ? "Turn Landline's sound off" : "Turn Landline's sound on"}
                  title={soundOn ? "Sound off" : "Sound on"}
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-[var(--radius-control)] text-ink-2 hover:bg-press hover:text-ink @[150px]:h-9 @[150px]:w-9"
                >
                  <Speaker on={soundOn} />
                </button>
              )}
            </figcaption>
          </motion.figure>
        )}
      </AnimatePresence>
    </>
  );
}

function Speaker({ on }: { on: boolean }) {
  return (
    <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" stroke="none" />
      {on ? (
        <>
          <path d="M15.5 9a4 4 0 0 1 0 6" />
          <path d="M18 6.5a7.5 7.5 0 0 1 0 11" />
        </>
      ) : (
        <path d="M16 9.5l5 5M21 9.5l-5 5" />
      )}
    </svg>
  );
}
